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
    def __init__(self, transform_list,over_lap_mask=True):
        self.transform_list = transform_list
        self.over_lap_mask = over_lap_mask

    def _get_crop_box(self,image):
        return transforms.RandomResizedCrop.get_params(image,scale=(0.08, 1.0), ratio=(3.0/4.0,4.0/3.0))

    def __call__(self,sample,mask):
        i1, j1, h1, w1 = self._get_crop_box(sample)
        i2, j2, h2, w2 = self._get_crop_box(sample)
        #assert i1 != i2, f"{(i1, j1, h1, w1)}!={i2, j2, h2, w2}"
        i_min = max(i1,i2)
        i_max = min(i1+h1,i2+h2)
        j_min = max(j1,j2)
        j_max = min(j1+w1,j2+w2)
        h = i_max-i_min
        w = j_max-j_min
        area = h * w if h > 0 and w > 0 else 0
        # assert h > 0 
        # assert w > 0
        # print((i1, j1, h1, w1),(i2, j2, h2, w2))
        # assert area > 0 # Must postive intersection area between views
        #assert()
        #breakpoint()
        if self.over_lap_mask:
            intersect_masks = torch.zeros_like(mask)
            if area > 0:
                intersect_masks[:,i_min:i_max,j_min:j_max] = 1
            #breakpoint()
            mask = intersect_masks * mask
        #assert mask.sum() > 0
        assert len(self.transform_list) == 2
        output0,mask0 = self.transform_list[0](sample,mask,(i1, j1, h1, w1))
        output1,mask1 = self.transform_list[1](sample,mask,(i2, j2, h2, w2))
        output_cat = torch.stack([output0,output1], dim=0)
        mask_cat = torch.stack([mask0,mask1])
        
        return output_cat,mask_cat

class SSLMaskDataset(VisionDataset):
    def __init__(self, root: str, mask_file: str, extensions = IMG_EXTENSIONS, transform = None,mask_file_path=''):
        self.root = root
        self.transform = transform
        #self.samples = make_dataset(self.root, extensions = extensions,) #Pytorch 1.9+
        with open('1percent.txt') as f:
            samples = f.readlines()
            samples = [x.replace('\n','').strip() for x in samples ]
            samples = [x for x in samples if x]
            samples = [(os.path.join(root,x.split('_')[0],x),None) for x in samples]
            #samples = [x for x in samples if os.path.exists(x[0])]
           
            print("total files:",len(samples))
            self.samples = samples
        self.loader = default_loader
        self.img_to_mask = self._get_masks(mask_file)
        self.mask_file_path = mask_file_path

    def _get_masks(self, mask_file):
        with open(mask_file, "rb") as file:
            return pickle.load(file)
        
    def __getitem__(self, index: int):
        path, _ = self.samples[index]
        
        # Load Image
        sample = self.loader(path)
        
        # Load Mask
        mask_file_name = self.img_to_mask[index].split('/')[-1]
        mask_file_path = os.path.join(self.mask_file_path,mask_file_name)
        with open(mask_file_path, "rb") as file:
            mask = pickle.load(file)
            mask += 1 # no zero, reserved for nothing
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
        mask = np.max(np.stack([self.coco.annToMask(ann) * (idx + 1)
                                                 for idx,ann in enumerate(anns)]), axis=0) #instance
        # idx==0 is background
        # print(np.unique(mask))
        # return sample,mask
        # Apply transforms
        mask = torch.LongTensor(mask)
        if self.transform is not None:
            sample,mask = self.transform(sample,mask.unsqueeze(0))
        #breakpoint()
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
        
    def __call__(self, img, mask,cordinates):
        for p in self.p_list:
            if isinstance(p,MaskRandomResizedCrop):
                img,mask = p(img,mask,cordinates)
            else:
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
        
    def __call__(self, image, mask,cordinates):
        
        """
        Args:
            image (PIL Image or Tensor): Image to be cropped and resized.
            mask (Tensor): Mask to be cropped and resized.
        Returns:
            PIL Image or Tensor: Randomly cropped/resized image.
            Mask Tensor: Randomly cropped/resized mask.
        """
        #import ipdb;ipdb.set_trace()
        i,j,h,w = cordinates
        #i, j, h, w = transforms.RandomResizedCrop.get_params(image,scale=(0.08, 1.0), ratio=(3.0/4.0,4.0/3.0))
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

def get_transform(stage, gb_prob=1.0, solarize_prob=0., crop_size=224,crop_cordinates=None):
    #i, j, h, w = crop_cordinates
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
