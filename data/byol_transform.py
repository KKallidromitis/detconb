#-*- coding:utf-8 -*-
import torch
from torchvision import transforms
import cv2
from PIL import Image, ImageOps
import numpy as np
import pickle
from torchvision.datasets import VisionDataset
from torchvision.datasets.folder import default_loader,make_dataset,IMG_EXTENSIONS
from pycocotools.coco import COCO
import os

class MultiViewDataInjector():
    def __init__(self, transform_list):
        self.transform_list = transform_list

    def __call__(self,sample,mask):
        output,mask = zip(*[transform(sample,mask) for transform in self.transform_list])
        output_cat = torch.stack(output, dim=0)
        mask_cat = torch.stack(mask)
        
        return output_cat,mask_cat

class SSLMaskDataset(VisionDataset):
    def __init__(self, root: str, mask_file: str, extensions = IMG_EXTENSIONS, transform = None):
        self.root = root
        self.transform = transform
        self.samples = make_dataset(self.root, extensions = extensions) #Pytorch 1.9+
        self.loader = default_loader
        self.img_to_mask = self._get_masks(mask_file)

    def _get_masks(self, mask_file):
        with open(mask_file, "rb") as file:
            return pickle.load(file)
        
    def __getitem__(self, index: int):
        path, _ = self.samples[index]
        
        # Load Image
        sample = self.loader(path)
        
        # Load Mask
        with open(self.img_to_mask[index], "rb") as file:
            mask = pickle.load(file)

        # Apply transforms
        if self.transform is not None:
            sample,mask = self.transform(sample,mask.unsqueeze(0))
        return sample,mask

    def __len__(self) -> int:
        return len(self.samples)

class COCOMaskDataset(VisionDataset):
    def __init__(self, root: str,annFile: str, transform = None):
        self.root = root
        self.coco = COCO(annFile)
        self.transform = transform
        #self.samples = make_dataset(self.root, extensions = extensions) #Pytorch 1.9+
        self.loader = default_loader
        ids = []
        # perform filter 
        for k in self.coco.imgs.keys():
            anns = self.coco.loadAnns(self.coco.getAnnIds(k))
            if len(anns)>0:
                ids.append(k)
        self.ids = list(sorted(ids))
        #self.img_to_mask = self._get_masks(mask_file)

    def _get_masks(self, mask_file):
        with open(mask_file, "rb") as file:
            return pickle.load(file)
        
    def __getitem__(self, index: int):
        id = self.ids[index]
        filename = self.coco.loadImgs(id)[0]["file_name"]
        path = os.path.join(self.root, filename)
        # Load Image
        sample = self.loader(path)
        anns = self.coco.loadAnns(self.coco.getAnnIds(id))
        mask = np.max(np.stack([self.coco.annToMask(ann) * ann["category_id"] 
                                                 for ann in anns]), axis=0)

        # print(np.unique(mask))
        # return sample,mask
        # Apply transforms
        mask = torch.LongTensor(mask)
        if self.transform is not None:
            sample,mask = self.transform(sample,mask.unsqueeze(0))
        return sample,mask

    def __len__(self) -> int:
        return len(self.ids)


class GaussianBlur():
    def __init__(self, kernel_size, sigma_min=0.1, sigma_max=2.0):
        self.sigma_min = sigma_min
        self.sigma_max = sigma_max
        self.kernel_size = kernel_size

    def __call__(self, img):
        sigma = np.random.uniform(self.sigma_min, self.sigma_max)
        img = cv2.GaussianBlur(np.array(img), (self.kernel_size, self.kernel_size), sigma)
        return Image.fromarray(img.astype(np.uint8))

class CustomCompose:
    def __init__(self, t_list,p_list):
        self.t_list = t_list
        self.p_list = p_list
        
    def __call__(self, img, mask):
        for p in self.p_list:
            img,mask = p(img,mask)
        for t in self.t_list:
            img = t(img)
        return img,mask

    def __repr__(self) -> str:
        format_string = self.__class__.__name__ + "("
        for t in self.t_list:
            format_string += "\n"
            format_string += f"    {t}"
        format_string += "\n)"
        return format_string
    
class MaskRandomResizedCrop():
    def __init__(self, size):
        super().__init__()
        self.size = size
        self.totensor = transforms.ToTensor()
        self.topil = transforms.ToPILImage()
        
    def __call__(self, image, mask):
        
        """
        Args:
            image (PIL Image or Tensor): Image to be cropped and resized.
            mask (Tensor): Mask to be cropped and resized.
        Returns:
            PIL Image or Tensor: Randomly cropped/resized image.
            Mask Tensor: Randomly cropped/resized mask.
        """
        #import ipdb;ipdb.set_trace()
        i, j, h, w = transforms.RandomResizedCrop.get_params(image,scale=(0.08, 1.0), ratio=(3.0/4.0,4.0/3.0))
        image = transforms.functional.resize(transforms.functional.crop(image, i, j, h, w),(self.size,self.size),interpolation=transforms.functional.InterpolationMode.BICUBIC)
        
        image = self.topil(torch.clip(self.totensor(image),min=0, max=255))
        mask = transforms.functional.resize(transforms.functional.crop(mask, i, j, h, w),(self.size,self.size),interpolation=transforms.functional.InterpolationMode.NEAREST)
        
        return [image,mask]
    
class MaskRandomHorizontalFlip():
    """
    Apply horizontal flip to a PIL Image and Mask.
    """

    def __init__(self, p=0.5):
        super().__init__()
        self.p = p

    def __call__(self, image, mask):
        """
        Args:
            image (PIL Image or Tensor): Image to be flipped.
            mask (Tensor): Mask to be flipped.
        Returns:
            PIL Image or Tensor: Randomly flipped image.
            Mask Tensor: Randomly flipped mask.
        """
        
        if torch.rand(1) < self.p:
            image = transforms.functional.hflip(image)
            mask = transforms.functional.hflip(mask)
            return [image,mask]
        return [image,mask]
    
    
class Solarize():
    def __init__(self, threshold=128):
        self.threshold = threshold

    def __call__(self, sample):
        return ImageOps.solarize(sample, self.threshold)

def get_transform(stage, gb_prob=1.0, solarize_prob=0., crop_size=224):
    t_list = []
    color_jitter = transforms.ColorJitter(0.4, 0.4, 0.2, 0.1)
    normalize = transforms.Normalize(mean=[0.485, 0.456, 0.406],
                                     std=[0.229, 0.224, 0.225])
    if stage in ('train', 'val'):
        t_list = [
            transforms.RandomApply([color_jitter], p=0.8),
            transforms.RandomGrayscale(p=0.2),
            transforms.RandomApply([GaussianBlur(kernel_size=23)], p=gb_prob),
            transforms.RandomApply([Solarize()], p=solarize_prob),
            transforms.ToTensor(),
            normalize]
        
        p_list = [
            MaskRandomResizedCrop(crop_size),
            MaskRandomHorizontalFlip(),
        ]
        
    elif stage == 'ft':
        t_list = [
            transforms.ToTensor(),
            normalize]
        
        p_list = [
            MaskRandomResizedCrop(crop_size),
            MaskRandomHorizontalFlip(),
        ]
            
    elif stage == 'test':
        t_list = [
            transforms.ToTensor(),
            normalize]
        
        p_list = [
            transforms.Resize(256),
            transforms.CenterCrop(crop_size),
        ]
        
    transform = CustomCompose(t_list,p_list)
    return transform
