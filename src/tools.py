import base64
import json
import os

from PIL import Image


def resize_image(source_path, save_path, target_size=1024):
    img = Image.open(source_path)
    width, height = img.size
    max_size = max(width, height)
    ratio = target_size / max_size
    new_width = int(width * ratio)
    new_height = int(height * ratio)
    resized_img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    resized_img.save(save_path)


def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")


def process_response(text):
    start = text.find("[")
    end = text.rfind("]")
    return json.loads(text[start : end + 1])
