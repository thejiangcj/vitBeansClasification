import functools
import glob
import os
import time

from clu import metric_writers

import numpy as np

import jax
import jax.numpy as jnp

import flax
import flax.optim as optim
import flax.jax_utils as flax_utils

import tensorflow as tf

from vitBeans import checkpoint
from vitBeans import flags
from vitBeans import hyper
from vitBeans import loging
from vitBeans import input_pipeline
from vitBeans import models
from vitBeans import momentum_clip


def make_update_fn(vit_fn, accum_steps):

  @functools.partial(jax.pmap, axis_name='batch', donate_argnums=(0,))
  def update_fn(opt, lr, batch, update_rng):
    update_rng = jax.random.fold_in(update_rng, jax.lax.axis_index('batch'))
    update_rng, new_update_rng = jax.random.split(update_rng)

    def cross_entropy_loss(*, logits, labels):
      logp = jax.nn.log_softmax(logits)
      return -jnp.mean(jnp.sum(logp * labels, axis=1))

    def loss_fn(params, images, labels):
      with flax.nn.stochastic(update_rng):
        logits = vit_fn(params, images, train=True)
      return cross_entropy_loss(logits=logits, labels=labels)

    l, g = hyper.accumulate_gradient(
        jax.value_and_grad(loss_fn), opt.target, batch['image'], batch['label'],
        accum_steps)
    g = jax.tree_map(lambda x: jax.lax.pmean(x, axis_name='batch'), g)

    opt = opt.apply_gradient(g, learning_rate=lr)
    return opt, l, new_update_rng

  return update_fn


def main(args):
  logdir = os.path.join(args.logdir, args.name)
  logger = loging.setup_logger(logdir)
  logger.info(args)

  logger.info(f'Available devices: {jax.devices()}')

  #数据导入
  dataset_info = input_pipeline.get_dataset_info(args.dataset, 'train')

  ds_train = input_pipeline.get_data(
      dataset=args.dataset,
      mode='train',
      repeats=None,
      mixup_alpha=args.mixup_alpha,
      batch_size=args.batch,
      shuffle_buffer=args.shuffle_buffer,
      tfds_data_dir=args.tfds_data_dir,
      tfds_manual_dir=args.tfds_manual_dir)
  batch = next(iter(ds_train))
  logger.info(ds_train)
  ds_test = input_pipeline.get_data(
      dataset=args.dataset,
      mode='test',
      repeats=1,
      batch_size=args.batch_eval,
      tfds_data_dir=args.tfds_data_dir,
      tfds_manual_dir=args.tfds_manual_dir)
  logger.info(ds_test)
  ds_val = input_pipeline.get_data(
      dataset=args.dataset,
      mode='val',
      repeats=1,
      batch_size=args.batch_eval,
      tfds_data_dir=args.tfds_data_dir,
      tfds_manual_dir=args.tfds_manual_dir)
  logger.info(ds_val)

  # 实例化网络架构
  model = models.KNOWN_MODELS[args.model]
  VisionTransformer = model.partial(num_classes=dataset_info['num_classes'])
  _, params = VisionTransformer.init_by_shape(
      jax.random.PRNGKey(0),

      [(batch['image'].shape[1:], batch['image'].dtype.name)])

  pretrained_path = os.path.join(args.vit_pretrained_dir, f'{args.model}.npz')
  params = checkpoint.load_pretrained(
      pretrained_path=pretrained_path,
      init_params=params,
      model_config=models.CONFIGS[args.model],
      logger=logger)

  vit_fn_repl = jax.pmap(VisionTransformer.call)
  update_fn_repl = make_update_fn(VisionTransformer.call, args.accum_steps)

  opt = momentum_clip.Optimizer(
      dtype=args.optim_dtype, grad_norm_clip=args.grad_norm_clip).create(params)
  opt_repl = flax_utils.replicate(opt)

  del opt
  del params

  def copyfiles(paths):
    
    if not args.copy_to:
      return
    for path in paths:
      to_path = os.path.join(args.copy_to, args.name, os.path.basename(path))
      tf.io.gfile.makedirs(os.path.dirname(to_path))
      tf.io.gfile.copy(path, to_path, overwrite=True)
      logger.info(f'Copied {path} to {to_path}.')

  total_steps = args.total_steps or (
      input_pipeline.DATASET_PRESETS[args.dataset]['total_steps'])

  lr_fn = hyper.create_learning_rate_schedule(total_steps, args.base_lr,
                                              args.decay_type,
                                              args.warmup_steps)
  lr_iter = hyper.lr_prefetch_iter(lr_fn, 0, total_steps)
  update_rngs = jax.random.split(
      jax.random.PRNGKey(0), jax.local_device_count())

  writer = metric_writers.create_default_writer(logdir, asynchronous=False)
  writer.write_hparams({k: v for k, v in vars(args).items() if v is not None})
  logger.info('Starting training loop; initial compile can take a while...')
  t0 = time.time()

  for step, batch, lr_repl in zip(
      range(1, total_steps + 1),
      input_pipeline.prefetch(ds_train, args.prefetch), lr_iter):

    opt_repl, loss_repl, update_rngs = update_fn_repl(
        opt_repl, lr_repl, batch, update_rngs)

    if step == 1:
      logger.info(f'First step took {time.time() - t0:.1f} seconds.')
      t0 = time.time()
    if args.progress_every and step % args.progress_every == 0:
      writer.write_scalars(step, dict(train_loss=float(loss_repl[0])))
      done = step / total_steps
      logger.info(f'Step: {step}/{total_steps} {100*done:.1f}%, '
                  f'ETA: {(time.time()-t0)/done*(1-done)/3600:.2f}h')
      copyfiles(glob.glob(f'{logdir}/*'))

    # 评估，计算准确率
    if ((args.eval_every and step % args.eval_every == 0) or
        (step == total_steps)):

      accuracy_test = np.mean([
          c for batch in input_pipeline.prefetch(ds_test, args.prefetch)
          for c in (
              np.argmax(vit_fn_repl(opt_repl.target, batch['image']),
                        axis=2) == np.argmax(batch['label'], axis=2)).ravel()
      ])

      accuracy_val = np.mean([
          c for batch in input_pipeline.prefetch(ds_val, args.prefetch)
          for c in (
              np.argmax(vit_fn_repl(opt_repl.target, batch['image']),
                        axis=2) == np.argmax(batch['label'], axis=2)).ravel()
      ])

      lr = float(lr_repl[0])
      logger.info(f'Step: {step} '
                  f'Learning rate: {lr:.7f}, '
                  f'val accuracy: {accuracy_val:0.5f},'
                  f'Test accuracy: {accuracy_test:0.5f}')
      writer.write_scalars(step, dict(accuracy_val=accuracy_val, accuracy_test=accuracy_test, lr=lr))
      copyfiles(glob.glob(f'{logdir}/*'))

  if args.output:
    checkpoint.save(flax_utils.unreplicate(opt_repl.target), args.output)
    logger.info(f'Stored fine tuned checkpoint to {args.output}')
    copyfiles([args.output])


if __name__ == '__main__':
  # Make sure tf does not allocate gpu memory.
  tf.config.experimental.set_visible_devices([], 'GPU')

  parser = flags.argparser(models.KNOWN_MODELS.keys(),
                           input_pipeline.DATASET_PRESETS.keys())

  main(parser.parse_args())
