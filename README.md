# vitBeansClasification
vit 在数据集 Beans 豆类植物病害分类应用

数据集地址：[Beans](https://github.com/AI-Lab-Makerere/ibean/)

code refer and fork from: [vision_transformer](https://github.com/google-research/vision_transformer) 

## Introduction

> 参考 paper: [An Image is Worth 16x16 Words: Transformers for Image Recognition at Scale](https://arxiv.org/abs/2010.11929)



## Training

### Way 1: Jupyter Notebook in Colab

You can run in [Colab Link](https://colab.research.google.com/drive/1UaGJryx8gJAgLICg_N5tCHVVcII6-atH?usp=sharing)

### Way 2: Python Script(Recommendation)

Refer to the above Colab link's [Step 4](https://colab.research.google.com/drive/1UaGJryx8gJAgLICg_N5tCHVVcII6-atH?usp=sharing#scrollTo=YcQn0Cxketnr)

1. Run Step1 - Link to your Google Colab Drive and Install Python res ([Jax](https://github.com/google/jax) and the like)
2. Run Step2.1 - Download VIT model for transfering learning
3. Run Step4 - Training model

## Cite

```latex
@article{dosovitskiy2020,
  title={An Image is Worth 16x16 Words: Transformers for Image Recognition at Scale},
  author={Dosovitskiy, Alexey and Beyer, Lucas and Kolesnikov, Alexander and Weissenborn, Dirk and Zhai, Xiaohua and Unterthiner, Thomas and  Dehghani, Mostafa and Minderer, Matthias and Heigold, Georg and Gelly, Sylvain and Uszkoreit, Jakob and Houlsby, Neil},
  journal={arXiv preprint arXiv:2010.11929},
  year={2020}
}

@ONLINE {beansdata,
    author="Makerere AI Lab",
    title="Bean disease dataset",
    month="January",
    year="2020",
    url="https://github.com/AI-Lab-Makerere/ibean/"
}
```



