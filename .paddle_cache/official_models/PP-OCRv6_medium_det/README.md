---
license: apache-2.0
library_name: PaddleOCR
language:
- en
- zh
pipeline_tag: image-to-text
tags:
- OCR
- PaddlePaddle
- PaddleOCR
- textline_detection
---

<div align="center">


<h1 align="center">

PP-OCRv6: From 1.5M to 34.5M Parameters, Surpassing Billion-Scale VLMs on OCR Tasks

</h1>

[![repo](https://img.shields.io/github/stars/PaddlePaddle/PaddleOCR?color=ccf)](https://github.com/PaddlePaddle/PaddleOCR)


**🔥 [Official Website](https://www.paddleocr.com)**
**📝 [Technical Report](https://arxiv.org/pdf/2606.13108)**

</div>

<div align="center">
<img src="https://cdn-uploads.huggingface.co/production/uploads/684ba591e717a30275a1b76a/0XIrg0UmmOvplnPjmsmK3.png" width="800"/>
</div>

## PP-OCRv6 Overview

PP-OCRv6 is a lightweight OCR system that combines architectural innovation with data-centric optimization. It redesigns the backbone, detection neck, and recognition neck around a unified MetaFormer-style building block with structural reparameterization. Three model tiers (medium, small, tiny) share the same block primitives, covering deployment scenarios from server to edge.

### Key Features

1. **Unified and Scalable Model Family:** A three-tier OCR model family spanning 1.5M to 34.5M parameters. PP-OCRv6_medium achieves 86.2% detection Hmean and 83.2% recognition accuracy, outperforming PP-OCRv5_server by +4.6% and +5.1% respectively.

2. **Lightweight Architectural Innovations:** (i) LCNetV4, a MetaFormer-style lightweight backbone with structural reparameterization; (ii) RepLKFPN, a detection neck with dilated reparameterizable depthwise convolutions; (iii) EncoderWithLightSVTR, a recognition neck with local-global attention and additive skip connections.

3. **Multi-Language and Scenario Support:** Supports 48 languages and diverse industrial scenes (digital displays, dot-matrix characters, tire prints, etc.), surpassing Qwen3-VL-235B, GPT-5.5, and Gemini-3.1-Pro with orders of magnitude fewer parameters.


# PP-OCRv6_medium_det

## Introduction

<div align="center">
<img src="https://cdn-uploads.huggingface.co/production/uploads/684ba591e717a30275a1b76a/ofnSGExgJL6K6d8ghh0vl.png" width="600"/>

PP-OCRv6 text detection architecture overview
</div>

PP-OCRv6_medium_det is the largest model in the PP-OCRv6 detection series developed by the PaddleOCR team. It uses LCNetV4 as the backbone and RepLKFPN as the feature pyramid neck, providing accurate text localization across diverse scenarios including handwritten, printed, rotated, curved, and artistic text in multiple languages. The model contains 15.5M parameters. The key accuracy metrics are as follows:

| Model | Average | Handwritten CN | Handwritten EN | Printed CN | Printed EN | Traditional Chinese | Ancient Text | Japanese | Blur | Emoji | Warp | Pinyin | Artistic | Table | Rotation | Industrial | General |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Gemini-3.1-Pro | 46.8 | 53.4 | 56.5 | 47.3 | 47.6 | 39.0 | 45.8 | 38.2 | 50.0 | 68.1 | 44.6 | 40.6 | 65.2 | 26.9 | 22.1 | 52.5 | 50.2 |
| GPT-5.5 | 45.6 | 42.4 | 58.5 | 50.2 | 51.9 | 35.0 | 26.7 | 42.0 | 49.1 | 97.5 | 37.7 | 36.3 | 52.0 | 71.0 | 10.0 | 36.2 | 32.6 |
| Qwen3-VL-235B | 38.3 | 56.5 | 66.0 | 41.7 | 37.0 | 19.3 | 13.1 | 27.0 | 38.5 | 81.2 | 28.5 | 33.0 | 68.3 | 19.6 | 2.1 | 48.4 | 32.3 |
| Kimi-K2.6 | 12.8 | 12.5 | 25.5 | 10.1 | 18.5 | 8.2 | 7.5 | 11.2 | 16.9 | 28.9 | 13.9 | 6.8 | 16.1 | 10.9 | 0.8 | 6.3 | 10.9 |
| MiniMax-M3 | 12.0 | 13.7 | 19.3 | 9.8 | 14.1 | 7.7 | 11.1 | 10.6 | 16.1 | 32.8 | 12.8 | 8.5 | 16.6 | 5.5 | 0.1 | 6.4 | 6.4 |
| PP-OCRv5_server | 81.6 | 80.3 | 84.1 | 94.5 | 91.7 | 81.5 | 67.6 | 77.2 | 90.1 | 96.2 | 87.6 | 67.1 | 67.3 | 97.1 | 80.0 | 64.3 | 79.7 |
| PP-OCRv5_mobile | 75.2 | 74.4 | 77.7 | 90.5 | 91.0 | 82.3 | 58.1 | 72.7 | 87.4 | 93.6 | 82.7 | 57.5 | 52.5 | 92.8 | 64.7 | 52.8 | 72.1 |
| **PP-OCRv6_medium** | **86.2** | **83.7** | **84.0** | **95.1** | **93.7** | **86.3** | **80.2** | **84.3** | **94.1** | **99.6** | **88.6** | **74.0** | **69.0** | **96.8** | **93.8** | **73.3** | **82.8** |
| PP-OCRv6_small | 84.1 | 80.5 | 87.1 | 94.2 | 93.6 | 85.7 | 72.6 | 82.3 | 92.6 | 99.7 | 87.6 | 69.6 | 65.3 | 95.6 | 93.7 | 67.6 | 78.2 |
| PP-OCRv6_tiny | 80.6 | 79.4 | 85.9 | 93.1 | 92.3 | 83.7 | 63.0 | 76.6 | 89.3 | 99.8 | 86.1 | 59.0 | 60.1 | 94.7 | 91.0 | 62.0 | 73.8 |

## Quick Start

### Installation

```bash
# Install the basic version
pip install paddleocr

# Install the full version (includes all features)
pip install "paddleocr[all]"
```

> This model uses the `paddle_static` inference engine by default. Please complete [PaddlePaddle installation](https://www.paddlepaddle.org.cn/en/install/quick) before use.

### Model Usage

You can quickly experience the functionality with a single command:

```bash
paddleocr text_detection \
    --model_name PP-OCRv6_medium_det \
    -i https://cdn-uploads.huggingface.co/production/uploads/681c1ecd9539bdde5ae1733c/3ul2Rq4Sk5Cn-l69D695U.png
```

You can also integrate the model inference of the text detection module into your project. Before running the following code, please download the sample image to your local machine.

```python
from paddleocr import TextDetection
model = TextDetection(model_name="PP-OCRv6_medium_det")
output = model.predict(input="3ul2Rq4Sk5Cn-l69D695U.png", batch_size=1)
for res in output:
    res.print()
    res.save_to_img(save_path="./output/")
    res.save_to_json(save_path="./output/res.json")
```

<!-- TODO: Update document links to PP-OCRv6 official documentation when available -->
For details about usage command and descriptions of parameters, please refer to the [Document](https://paddlepaddle.github.io/PaddleOCR/latest/en/version3.x/module_usage/text_detection.html#iii-quick-start).

### Pipeline Usage

The general OCR pipeline extracts text information from images. The pipeline consists of several modules:
* Document Image Orientation Classification Module (Optional)
* Text Image Unwarping Module (Optional)
* Text Line Orientation Classification Module (Optional)
* Text Detection Module
* Text Recognition Module

Run a single command to quickly experience the OCR pipeline:

```bash
paddleocr ocr -i https://cdn-uploads.huggingface.co/production/uploads/681c1ecd9539bdde5ae1733c/3ul2Rq4Sk5Cn-l69D695U.png \
    --text_detection_model_name PP-OCRv6_medium_det \
    --text_recognition_model_name PP-OCRv6_medium_rec \
    --use_doc_orientation_classify False \
    --use_doc_unwarping False \
    --use_textline_orientation True \
    --save_path ./output \
    --device gpu:0
```

For project integration:

```python
from paddleocr import PaddleOCR

ocr = PaddleOCR(
    text_detection_model_name="PP-OCRv6_medium_det",
    text_recognition_model_name="PP-OCRv6_medium_rec",
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False,
)
result = ocr.predict("./3ul2Rq4Sk5Cn-l69D695U.png")
for res in result:
    res.print()
    res.save_to_img("output")
    res.save_to_json("output")
```

<!-- TODO: Update document links to PP-OCRv6 official documentation when available -->
For details about usage command and descriptions of parameters, please refer to the [Document](https://paddlepaddle.github.io/PaddleOCR/latest/en/version3.x/pipeline_usage/OCR.html#2-quick-start).

## Links

[PaddleOCR Repo](https://github.com/paddlepaddle/paddleocr)

[PaddleOCR Documentation](https://paddlepaddle.github.io/PaddleOCR/latest/en/index.html)

## Citation

```bibtex
@misc{zhang2026ppocrv6,
  title={PP-OCRv6: From 1.5M to 34.5M Parameters, Surpassing Billion-Scale VLMs on OCR Tasks},
  author={Yubo Zhang and Xueqing Wang and Manhui Lin and Yue Zhang and Penglongyi Deng and Ting Sun and Tingquan Gao and Zelun Zhang and Jiaxuan Liu and Changda Zhou and Hongen Liu and Suyin Liang and Cheng Cui and Yi Liu and Dianhai Yu and Yanjun Ma},
  year={2026},
  eprint={2606.13108},
  archivePrefix={arXiv},
  primaryClass={cs.CV},
  url={https://arxiv.org/abs/2606.13108},
}
```
