# Copyright 2020 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import flax
import jax
import numpy as np
import tensorflow as tf
import tensorflow_probability as tfp
import tensorflow_datasets as tfds

import sys
if sys.platform != 'darwin':
  import resource
  low, high = resource.getrlimit(resource.RLIMIT_NOFILE)
  resource.setrlimit(resource.RLIMIT_NOFILE, (high, high))

# Adjust depending on the available RAM.
MAX_IN_MEMORY = 200_000

DATASET_PRESETS = {
    'food101': {
        'train': 'train[:98%]',
        'test': 'validation',
        'resize': 512,
        'crop': 384,
        'total_steps': 10_000,
    },
    'Beans': {
        'train': 'train[:98%]',
        'val': 'validation',
        'test': 'test',
        'resize': 512,
        'crop': 384,
        'total_steps': 10_000,
    }
}


def get_dataset_info(dataset, split):
  data_builder = tfds.builder(dataset)
  num_examples = data_builder.info.splits[split].num_examples
  num_classes = data_builder.info.features['label'].num_classes
  return {
      'num_examples': num_examples,
      'num_classes': num_classes
  }


def get_data(*,
             dataset,
             mode,
             repeats,
             batch_size,
             mixup_alpha=0,
             shuffle_buffer=MAX_IN_MEMORY,
             tfds_data_dir=None,
             tfds_manual_dir=None,
             inception_crop=True):

  preset = DATASET_PRESETS.get(dataset)
  if preset is None:
    raise KeyError(f'Please add "{dataset}" to {__name__}.DATASET_PRESETS"')
  split = preset[mode]
  resize_size = preset['resize']
  crop_size = preset['crop']
  data_builder = tfds.builder(dataset, data_dir=tfds_data_dir)
  dataset_info = get_dataset_info(dataset, split)

  data_builder.download_and_prepare(
      download_config=tfds.download.DownloadConfig(manual_dir=tfds_manual_dir))
  data = data_builder.as_dataset(
      split=split,
      decoders={'image': tfds.decode.SkipDecoding()},
      shuffle_files=mode == 'train')
  decoder = data_builder.info.features['image'].decode_example

  def _pp(data):
    im = decoder(data['image'])
    if mode == 'train':
      if inception_crop:
        channels = im.shape[-1]
        begin, size, _ = tf.image.sample_distorted_bounding_box(
            tf.shape(im),
            tf.zeros([0, 0, 4], tf.float32),
            area_range=(0.05, 1.0),
            min_object_covered=0,  # Don't enforce a minimum area.
            use_image_if_no_bounding_boxes=True)
        im = tf.slice(im, begin, size)
        # Unfortunately, the above operation loses the depth-dimension. So we
        # need to restore it the manual way.
        im.set_shape([None, None, channels])
        im = tf.image.resize(im, [crop_size, crop_size])
        im = tf.image.random_flip_left_right(im)
        im = tf.image.random_flip_up_down(im)
        im = tf.image.random_brightness(im, 0.5)
        im = tf.image.random_contrast(im,0,1)
      else:
        im = tf.image.resize(im, [resize_size, resize_size])
        im = tf.image.random_crop(im, [crop_size, crop_size, 3])
      if tf.random.uniform(shape=[]) > 0.5:
        im = tf.image.flip_left_right(im)
    else:
      # usage of crop_size here is intentional
      im = tf.image.resize(im, [crop_size, crop_size])
    im = (im - 127.5) / 127.5
    label = tf.one_hot(data['label'], dataset_info['num_classes'])  # pylint: disable=no-value-for-parameter
    return {'image': im, 'label': label}

  data = data.repeat(repeats)
  if mode == 'train':
    data = data.shuffle(min(dataset_info['num_examples'], shuffle_buffer))
  data = data.map(_pp, tf.data.experimental.AUTOTUNE)
  data = data.batch(batch_size, drop_remainder=True)

  def _mixup(data):
    beta_dist = tfp.distributions.Beta(mixup_alpha, mixup_alpha)
    beta = tf.cast(beta_dist.sample([]), tf.float32)
    data['image'] = (
        beta * data['image'] + (1 - beta) * tf.reverse(data['image'], axis=[0]))
    data['label'] = (
        beta * data['label'] + (1 - beta) * tf.reverse(data['label'], axis=[0]))
    return data

  if mixup_alpha is not None and mixup_alpha > 0.0 and mode == 'train':
    data = data.map(_mixup, tf.data.experimental.AUTOTUNE)

  # Shard data such that it can be distributed accross devices
  num_devices = jax.local_device_count()

  def _shard(data):
    data['image'] = tf.reshape(data['image'],
                               [num_devices, -1, crop_size, crop_size, 3])
    data['label'] = tf.reshape(data['label'],
                               [num_devices, -1, dataset_info['num_classes']])
    return data

  if num_devices is not None:
    data = data.map(_shard, tf.data.experimental.AUTOTUNE)

  return data.prefetch(1)


def prefetch(dataset, n_prefetch):
  ds_iter = iter(dataset)
  ds_iter = map(lambda x: jax.tree_map(lambda t: np.asarray(memoryview(t)), x),
                ds_iter)
  if n_prefetch:
    ds_iter = flax.jax_utils.prefetch_to_device(ds_iter, n_prefetch)
  return ds_iter
