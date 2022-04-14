import numpy as np
import cv2
from typing import Dict, List



from utils.registry import Registry
from utils.textblock_mask import canny_flood, connected_canny_flood
from utils.imgproc_utils import enlarge_window

INPAINTERS = Registry('inpainters')
register_inpainter = INPAINTERS.register_module

from ..moduleparamparser import ModuleParamParser, DEFAULT_DEVICE
from ..textdetector import TextBlock

class InpainterBase(ModuleParamParser):

    inpaint_by_block = True
    def __init__(self, **setup_params) -> None:
        super().__init__(**setup_params)
        self.name = ''
        for key in INPAINTERS.module_dict:
            if INPAINTERS.module_dict[key] == self.__class__:
                self.name = key
                break
        self.setup_inpainter()

    def setup_inpainter(self):
        raise NotImplementedError

    def inpaint(self, img: np.ndarray, mask: np.ndarray, textblock_list: List[TextBlock] = None) -> np.ndarray:
        def extract_ballon_mask(img: np.ndarray, mask: np.ndarray) -> np.ndarray:
            # img = cv2.GaussianBlur(img,(3,3),cv2.BORDER_DEFAULT)
            h, w = img.shape[:2]
            text_sum = np.sum(mask)
            cannyed = cv2.Canny(img, 70, 140, L2gradient=True, apertureSize=3)
            br = cv2.boundingRect(cv2.findNonZero(mask))
            br_xyxy = [br[0], br[1], br[0] + br[2], br[1] + br[3]]

            cv2.rectangle(cannyed, (0, 0), (w-1, h-1), (255, 255, 255), 1, cv2.LINE_8)
            cannyed = cv2.bitwise_and(cannyed, 255 - mask)

            cons, _ = cv2.findContours(cannyed, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
            min_ballon_area = w * h
            ballon_mask = None
            non_text_mask = None
            for ii, con in enumerate(cons):
                br_c = cv2.boundingRect(con)
                br_c = [br_c[0], br_c[1], br_c[0] + br_c[2], br_c[1] + br_c[3]]
                if br_c[0] > br_xyxy[0] or br_c[1] > br_xyxy[1] or br_c[2] < br_xyxy[2] or br_c[3] < br_xyxy[3]:
                    continue
                tmp = np.zeros_like(cannyed)
                cv2.drawContours(tmp, cons, ii, (255, 255, 255), -1, cv2.LINE_8)
                if cv2.bitwise_and(tmp, mask).sum() >= text_sum:
                    con_area = cv2.contourArea(con)
                    if con_area < min_ballon_area:
                        min_ballon_area = con_area
                        ballon_mask = tmp
            if ballon_mask is not None:
                non_text_mask = cv2.bitwise_and(ballon_mask, 255 - mask)
            #     cv2.imshow('ballon', ballon_mask)
            #     cv2.imshow('non_text', non_text_mask)
            # cv2.imshow('im', img)
            # cv2.imshow('msk', mask)
            # cv2.imshow('br', mask[br_xyxy[1]:br_xyxy[3], br_xyxy[0]:br_xyxy[2]])
            # cv2.imshow('canny', cannyed)
                
            # cv2.waitKey(0)
            # return msk
            return ballon_mask, non_text_mask


        if not self.inpaint_by_block or textblock_list is None:
            return self._inpaint(img, mask)
        else:
            im_h, im_w = img.shape[:2]
            inpainted = np.copy(img)
            for blk in textblock_list:
                xyxy = blk.xyxy
                xyxy_e = enlarge_window(xyxy, im_w, im_h, ratio=1.5)
                im = inpainted[xyxy_e[1]:xyxy_e[3], xyxy_e[0]:xyxy_e[2]]
                msk = mask[xyxy_e[1]:xyxy_e[3], xyxy_e[0]:xyxy_e[2]]
                # ballon_msk, non_text_msk = extract_ballon_mask(im, msk)
                # if ballon_msk is not None:
                #     non_text_region = np.where(non_text_msk > 0)
                #     non_text_px = im[non_text_region]
                #     average_bg_color = np.mean(non_text_px, axis=0)
                #     std = np.std(non_text_px - average_bg_color, axis=0)
                #     print(average_bg_color, std)
                #     cv2.imshow('im', im)
                #     cv2.imshow('ballon', ballon_msk)
                #     cv2.imshow('non_text', non_text_msk)
                #     cv2.waitKey(0)
                inpainted[xyxy_e[1]:xyxy_e[3], xyxy_e[0]:xyxy_e[2]] = self._inpaint(im, msk)
            return inpainted

    def _inpaint(self, img: np.ndarray, mask: np.ndarray, textblock_list: List[TextBlock] = None) -> np.ndarray:
        raise NotImplementedError


@register_inpainter('opencv-tela')
class OpenCVInpainter(InpainterBase):

    def setup_inpainter(self):
        self.inpaint_method = lambda img, mask, *args, **kwargs: cv2.inpaint(img, mask, 3, cv2.INPAINT_NS)
    
    def _inpaint(self, img: np.ndarray, mask: np.ndarray, textblock_list: List[TextBlock] = None) -> np.ndarray:
        return self.inpaint_method(img, mask)


@register_inpainter('patchmatch')
class PatchmatchInpainter(InpainterBase):

    def setup_inpainter(self):
        from . import patch_match
        self.inpaint_method = lambda img, mask, *args, **kwargs: patch_match.inpaint(img, mask, patch_size=3)
    
    def _inpaint(self, img: np.ndarray, mask: np.ndarray, textblock_list: List[TextBlock] = None) -> np.ndarray:
        return self.inpaint_method(img, mask)


import torch
from utils.imgproc_utils import resize_keepasp
from .aot import AOTGenerator
AOTMODEL: AOTGenerator = None
AOTMODEL_PATH = 'data/models/aot_inpainter.ckpt'

def load_aot_model(model_path, device) -> AOTGenerator:
    model = AOTGenerator(in_ch=4, out_ch=3, ch=32, alpha=0.0)
    sd = torch.load(model_path, map_location = 'cpu')
    model.load_state_dict(sd['model'] if 'model' in sd else sd)
    model.eval().to(device)
    return model


@register_inpainter('aot')
class AOTInpainter(InpainterBase):

    setup_params = {
        'inpaint_size': {
            'type': 'selector',
            'options': [
                1024, 
                2048
            ], 
            'select': 2048
        }, 
        'device': {
            'type': 'selector',
            'options': [
                'cpu',
                'cuda'
            ],
            'select': DEFAULT_DEVICE
        },
        'description': 'manga-image-translator inpainter'
    }

    device = DEFAULT_DEVICE
    inpaint_size = 2048
    model: AOTGenerator = None

    def setup_inpainter(self):
        global AOTMODEL
        self.device = self.setup_params['device']['select']
        if AOTMODEL is None:
            self.model = AOTMODEL = load_aot_model(AOTMODEL_PATH, self.device)
        else:
            self.model = AOTMODEL
            self.model.to(self.device)
        self.inpaint_by_block = True if self.device == 'cuda' else False
        self.inpaint_size = int(self.setup_params['inpaint_size']['select'])

    def inpaint_preprocess(self, img: np.ndarray, mask: np.ndarray) -> np.ndarray:

        img_original = np.copy(img)
        mask_original = np.copy(mask)
        mask_original[mask_original < 127] = 0
        mask_original[mask_original >= 127] = 1
        mask_original = mask_original[:, :, None]

        new_shape = self.inpaint_size if max(img.shape[0: 2]) > self.inpaint_size else None

        img = resize_keepasp(img, new_shape, stride=None)
        mask = resize_keepasp(mask, new_shape, stride=None)

        im_h, im_w = img.shape[:2]
        pad_bottom = 128 - im_h if im_h < 128 else 0
        pad_right = 128 - im_w if im_w < 128 else 0
        mask = cv2.copyMakeBorder(mask, 0, pad_bottom, 0, pad_right, cv2.BORDER_REFLECT)
        img = cv2.copyMakeBorder(img, 0, pad_bottom, 0, pad_right, cv2.BORDER_REFLECT)

        img_torch = torch.from_numpy(img).permute(2, 0, 1).unsqueeze_(0).float() / 127.5 - 1.0
        mask_torch = torch.from_numpy(mask).unsqueeze_(0).unsqueeze_(0).float() / 255.0
        mask_torch[mask_torch < 0.5] = 0
        mask_torch[mask_torch >= 0.5] = 1

        if self.device == 'cuda':
            img_torch = img_torch.cuda()
            mask_torch = mask_torch.cuda()
        img_torch *= (1 - mask_torch)
        return img_torch, mask_torch, img_original, mask_original, pad_bottom, pad_right

    @torch.no_grad()
    def _inpaint(self, img: np.ndarray, mask: np.ndarray, textblock_list: List[TextBlock] = None) -> np.ndarray:

        im_h, im_w = img.shape[:2]
        img_torch, mask_torch, img_original, mask_original, pad_bottom, pad_right = self.inpaint_preprocess(img, mask)
        img_inpainted_torch = self.model(img_torch, mask_torch)
        img_inpainted = ((img_inpainted_torch.cpu().squeeze_(0).permute(1, 2, 0).numpy() + 1.0) * 127.5).astype(np.uint8)
        if pad_bottom > 0:
            img_inpainted = img_inpainted[:-pad_bottom]
        if pad_right > 0:
            img_inpainted = img_inpainted[:, :-pad_right]
        new_shape = img_inpainted.shape[:2]
        if new_shape[0] != im_h or new_shape[1] != im_w :
            img_inpainted = cv2.resize(img_inpainted, (im_w, im_h), interpolation = cv2.INTER_LINEAR)
        img_inpainted = img_inpainted * mask_original + img_original * (1 - mask_original)
        
        return img_inpainted

    def updateParam(self, param_key: str, param_content):
        super().updateParam(param_key, param_content)

        if param_key == 'device':
            param_device = self.setup_params['device']['select']
            self.model.to(param_device)
            self.device = param_device
            if param_device == 'cuda':
                self.inpaint_by_block = False
            else:
                self.inpaint_by_block = True

        elif param_key == 'inpaint_size':
            self.inpaint_size = int(self.setup_params['inpaint_size']['select'])