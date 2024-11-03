import logging
from datetime import datetime

from src.notion_api import NotionDB
from src.tools import encode_image, process_response, resize_image
from src.vlm import VLM


logger = logging.getLogger("logger")


class Pipeline:
    def __init__(self, cfg):
        self.cfg = cfg

        self.notion = NotionDB(
            notion_api=cfg["db"]["api_key"], db_id=cfg["db"]["db_id"]
        )

        self.vlm = VLM(
            base_url=cfg["vlm"]["base_url"],
            api_key=cfg["vlm"]["api_token"],
            model_name=cfg["vlm"]["model_name"],
        )

        with open(cfg["vlm"]["prompt_template_file"], "r") as prompt_file:
            self.prompt_template = prompt_file.read().strip()

        self.sampling_params = cfg["vlm"]["sampling_params"]

    def __call__(self, image_path, person):
        unique_categories = self.notion.get_unique_categories()
        logger.info(f"Unique categories: {unique_categories}")
        prompt = self.prompt_template.replace(
            "{CASHBACK_CATEGORIES}", str(unique_categories)
        )
        date = datetime.now().replace(day=1).strftime("%Y-%m-%d")

        resize_image(image_path, image_path)
        base64_image = encode_image(image_path)

        logger.info("Sending request to VLM")
        response = self.vlm.request(
            prompt=prompt,
            base64_image=base64_image,
            sampling_params=self.sampling_params,
        )
        logger.info(f"Recieved response from VLM: {response}")

        rows = process_response(response.choices[0].message.content)
        for row in rows:
            row["Person"] = person
            row["Date"] = date

        logger.info(f"Processed response from VLM: {rows}")
        for row in rows:
            self.notion.add_row_to_database(row)

        logger.info("Rows added to Notion")
        return rows
