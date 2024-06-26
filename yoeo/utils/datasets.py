from torch.utils.data import Dataset
import torch.nn.functional as F
import torch
import glob
import random
import os
import warnings
import numpy as np
from io import StringIO
from PIL import Image
from PIL import ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True


def pad_to_square(img, pad_value):
    c, h, w = img.shape
    dim_diff = np.abs(h - w)
    # (upper / left) padding and (lower / right) padding
    pad1, pad2 = dim_diff // 2, dim_diff - dim_diff // 2
    # Determine padding
    pad = (0, 0, pad1, pad2) if h <= w else (pad1, pad2, 0, 0)
    # Add padding
    img = F.pad(img, pad, "constant", value=pad_value)

    return img, pad


def resize(image, size):
    image = F.interpolate(image.unsqueeze(0), size=size, mode="nearest").squeeze(0)
    return image



class ImageFolder(Dataset):
    def __init__(self, folder_path, transform=None):
        self.files = sorted(glob.glob("%s/*.*" % folder_path))
        self.transform = transform

    def __getitem__(self, index):

        img_path = self.files[index % len(self.files)]
        img = np.array(
            Image.open(img_path).convert('RGB'),
            dtype=np.uint8)

        # Label Placeholder
        boxes = np.zeros((1, 5))
        segmaps = np.zeros_like(img)

        # Apply transforms
        if self.transform:
            img, _, _ = self.transform((img, boxes, segmaps))

        return img_path, img

    def __len__(self):
        return len(self.files)


class ListDataset(Dataset):
    def __init__(self, list_path, is_segment=True, is_detect=True, img_size=416, multiscale=True, transform=None):
        with open(list_path, "r") as file:
            self.img_files = file.readlines()

        self.label_files = []
        for path in self.img_files:
            image_dir = os.path.dirname(path)
            label_dir = "labels".join(image_dir.rsplit("images", 1))
            assert label_dir != image_dir, \
                f"Image path must contain a folder named 'images'! \n'{image_dir}'"
            label_file = os.path.join(label_dir, os.path.basename(path))
            label_file = os.path.splitext(label_file)[0] + '.txt'
            self.label_files.append(label_file)

        self.mask_files = []
        for path in self.img_files:
            image_dir = os.path.dirname(path)
            mask_dir = "yoeo_segmentations".join(image_dir.rsplit("images", 1))
            assert mask_dir != image_dir, \
                f"Image path must contain a folder named 'images'! \n'{image_dir}'"
            mask_file = os.path.join(mask_dir, os.path.basename(path))
            mask_file = os.path.splitext(mask_file)[0] + '.png'
            self.mask_files.append(mask_file)

        self.img_size = img_size
        self.max_objects = 100
        self.multiscale = multiscale
        self.min_size = self.img_size - 3 * 32
        self.max_size = self.img_size + 3 * 32
        self.batch_count = 0
        self.transform = transform
        self.is_segment=is_segment
        self.is_detect=is_detect

    def __getitem__(self, index):

        # ---------
        #  Image
        # ---------
        try:
            img_path = self.img_files[index % len(self.img_files)].rstrip()

            img = np.array(Image.open(img_path).convert('RGB'), dtype=np.uint8)
        except Exception:
            print(f"Could not read image '{img_path}'.")
            return

        # ---------
        #  Label
        # ---------
        if self.is_detect:
            #try:
            label_path = self.label_files[index % len(self.img_files)].rstrip()
            # ------------------
            #  Modified version
            # ------------------
            #boxlist = []
            #with open(label_path,"r") as f:
            #    for line in f:
            #        sl = line.split(" ")
            #        cl = float(sl[0])
            #        cx = (float(sl[1])+float(sl[3]))/2
            #        cy = (float(sl[2])+float(sl[6]))/2
            #        w = float(sl[3])-float(sl[1])
            #        h = float(sl[6])-float(sl[2])
            #        boxlist.append([cl,cx,cy,w,h])
            #boxes=np.array(boxlist).reshape(-1, 5)

            # Original
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    boxes = np.loadtxt(label_path).reshape(-1, 5)
            except Exception:
                print(f"Could not read label '{label_path}'.")
                return
        else:
            boxes = np.loadtxt(StringIO("0 0 0 0 0\n0 0 0 0 0"))

        # ---------
        #  Segmentation Mask
        # ---------
        if self.is_segment:
            try:
                mask_path = self.mask_files[index % len(self.img_files)].rstrip()
                # Load segmentation mask as numpy array
                mask = np.array(Image.open(mask_path).convert('RGB'))
                #max = np.max(mask)
                #print(f"Loading {mask_path} Max: {max}")
            except FileNotFoundError as e:
                print(f"Could not load mask '{mask_path}'.")
                return
        else:
            mask = np.zeros(img.shape,dtype=np.uint8)


        # -----------
        #  Transform
        # -----------
        if self.transform:
            try:
                img, bb_targets, mask_targets = self.transform(
                    (img, boxes, mask)
                )
            except Exception as e:
                print(f"Could not apply transform.")
                raise e
                return

        return img_path, img, bb_targets, mask_targets

    def collate_fn(self, batch):
        self.batch_count += 1

        # Drop invalid images
        batch = [data for data in batch if data is not None]

        paths, imgs, bb_targets, mask_targets = list(zip(*batch))

        # Selects new image size every tenth batch
        if self.multiscale and self.batch_count % 10 == 0:
            self.img_size = random.choice(
                range(self.min_size, self.max_size + 1, 32))

        # Resize images to input shape
        imgs = torch.stack([resize(img, self.img_size) for img in imgs])

        # Add sample index to targets
        for i, boxes in enumerate(bb_targets):
            boxes[:, 0] = i
        bb_targets = torch.cat(bb_targets, 0)

        # Stack masks and drop the 2 duplicated channels
        mask_targets = torch.stack([resize(mask, self.img_size)[0] for mask in mask_targets]).long()

        return paths, imgs, bb_targets, mask_targets

    def __len__(self):
        return len(self.img_files)
