## Repository Structure

```text
.
├── configs/
│   ├── default.yml                 # Base model and training configuration
│   ├── experiment_configs.yml      # Main CoDe experiment configuration
│   └── eval.yml                    # Evaluation and post-processing options
├── datasets/                       # WebDataset loading and augmentation
├── models/
│   └── tcl/
│       ├── codecomposition.py      # ImageTextCoDecomposition
│       ├── masker.py               # Image feature masker
│       ├── aspp.py                 # Multi-rate ASPP module
│       ├── noun_decomposition.py   # Base decomposition model
│       ├── prompter.py             # CLIP prompt learner
│       └── tcl.py                  # CLIP feature and mask utilities
├── segmentation/
│   ├── configs/                    # MMSegmentation dataset configurations
│   ├── datasets/                   # Custom evaluation datasets
│   └── evaluation/                 # Segmentation inference wrapper
├── scripts/
│   ├── train.sh
│   └── eval.sh
├── tools/                          # Dataset and mask preparation utilities
├── main.py                         # Training and evaluation entry point
└── requirements.txt
```

## Environment Setup

The code was developed with Python 3.10, PyTorch 1.12.1, MMCV 1.6.2, and
MMSegmentation 0.27.0.

```bash
conda create -n ovca python=3.10 -y
conda activate ovca

# Install a CUDA-compatible PyTorch build for your system.
pip install torch==1.12.1 torchvision==0.13.1

pip install -U openmim
pip install -r requirements.txt
python -m mim install mmcv-full==1.6.2 mmsegmentation==0.27.0
```

The first model initialization may download the OpenAI CLIP ViT-B/16
checkpoint. Make sure the machine has network access or place the checkpoint
where the local CLIP loader can find it.

Download the NLTK resources used by the noun parsing utility:

```bash
python - <<'PY'
import nltk
nltk.download("punkt")
nltk.download("averaged_perceptron_tagger")
PY
```

## Dataset Preparation

### Training data

The default configuration trains on a preprocessed CC3M dataset:

```yaml
data:
  dataset:
    meta:
      gcc3m:
        path: /path/to/CC3M_mask
        prefix: "cc3m-train-{0000..0575}.tar"
        length: 3000000
```

Each WebDataset sample is expected to contain four files with the same
basename:

```text
sample_id.jpg       # RGB image
sample_id.txt       # Original caption
sample_id.json      # List of category names
sample_id.npz       # Compressed masks, stored under the key "masks"
```

The mask array should have shape `[num_categories, height, width]`, and its
category order must match the order in `sample_id.json`. The current collate
function randomly selects up to two categories per sample. For a sample with
one category, it automatically adds the complementary background mask.

The repository includes `dataloader_test.py` for inspecting a WebDataset
shard. The paths in that file are examples and should be replaced before use.

### Evaluation data

The evaluation pipeline supports:

- PASCAL VOC
- PASCAL Context
- COCO-Stuff
- COCO-Object
- Cityscapes
- ADE20K

The evaluation dataset roots are currently written as absolute paths in
`segmentation/configs/_base_/datasets/*.py`. Update them before training or
evaluation. The expected directory layouts are:

```text
VOC2012/
├── JPEGImages/
├── SegmentationClass/
└── ImageSets/Segmentation/val.txt

VOC2010/
├── JPEGImages/
├── SegmentationClassContext/
└── ImageSets/SegmentationContext/val.txt

coco_stuff164k/
├── images/val2017/
└── annotations/val2017/

cityscapes/
├── leftImg8bit/val/
└── gtFine/val/

OVSS_test/
└── ADEChallengeData2016/
    ├── images/validation/
    └── annotations/validation/
```

### COCO-Object conversion

COCO-Object is derived from COCO-Stuff by keeping the object categories and
converting the annotations to the format expected by MMSegmentation:

```bash
python convert_dataset/convert_coco_object.py \
    /path/to/coco_stuff164k \
    --out_dir /path/to/coco_stuff164k
```

The generated annotation files use the suffix `_instanceTrainIds.png`.

## Configuration

The main experiment is defined in `configs/experiment_configs.yml`, which
inherits from `configs/default.yml`.

The model currently requires a ViT-based CLIP backbone. The `batch_size`
parameter is the batch size per GPU. The global batch size is calculated as:

```text
global_batch_size = batch_size_per_gpu * number_of_gpus
```

The training script scales the learning rate according to the global batch
size.

## Training

Training must be launched through PyTorch distributed execution, including
for a single GPU:

```bash
CUDA_VISIBLE_DEVICES=0 python -m torch.distributed.run \
    --nproc_per_node=1 \
    --master_port=13890 \
    main.py \
    --cfg configs/experiment_configs.yml \
    --method-name image_text_co_decomposition
```

For multi-GPU training:

```bash
CUDA_VISIBLE_DEVICES=0,1,2,3 python -m torch.distributed.run \
    --nproc_per_node=4 \
    --master_port=13890 \
    main.py \
    --cfg configs/experiment_configs.yml \
    --method-name image_text_co_decomposition
```

To resume training from a checkpoint, use the same configuration and omit
`--eval`:

```bash
CUDA_VISIBLE_DEVICES=0 python -m torch.distributed.run \
    --nproc_per_node=1 \
    --master_port=13890 \
    main.py \
    --cfg configs/experiment_configs.yml \
    --method-name image_text_co_decomposition \
    --resume /path/to/checkpoint.pth
```

## Pretrained Model

You can download the pre‑trained checkpoint from Google Drive:
[pre‑trained checkpoint](https://drive.google.com/file/d/1StzAKAXNIQYyGnVvaK-6IZsxJZh-lPOD/view?usp=drive_link)

After downloading, place the `.pth` checkpoint file to your local path,
which can be used for evaluation or resuming training via `--resume` argument.

## Evaluation

When `--resume` and `--eval` are provided together, the program loads the
checkpoint configuration, merges it with `configs/eval.yml`, and evaluates the
configured benchmark list.

```bash
CUDA_VISIBLE_DEVICES=0 python -m torch.distributed.run \
    --nproc_per_node=1 \
    --master_port=13050 \
    main.py \
    --method-name image_text_co_decomposition \
    --resume /path/to/checkpoint.pth \
    --eval
```

The default evaluation tasks are configured in `configs/eval.yml`:

```yaml
task:
  - voc
  - context
  - coco_stuff
  - coco_object
  - cityscapes
  - ade20k
```

Edit this list and the dataset roots when evaluating only a subset of
benchmarks. Evaluation uses sliding-window inference with a default crop size
of `448 x 448` and stride `224 x 224`.

## Outputs

Each run creates an output directory similar to:

```text
output/
└── image_text_co_decomposition_b64_YYMMDD_HHMMSS/
    ├── config.json
    ├── log.txt
    ├── checkpoint.pth
    └── ckpt_<step>_miou<score>.pth
```

`checkpoint.pth` stores the latest training state. The `ckpt_*.pth` files are
the checkpoints selected by the top-k checkpoint manager.

## Notes

- The shell scripts in `scripts/` contain machine-specific GPU selections and
  checkpoint paths. Treat them as templates.
- The dataset preparation utilities in `tools/` also contain example absolute
  paths and should be edited before use.
- Training and evaluation are CUDA-oriented and rely on distributed
  initialization.
- The repository contains experimental loss-analysis files under `loss/`;
  these are not required for the main training or evaluation pipeline.
