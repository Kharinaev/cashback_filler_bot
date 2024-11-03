from openai import OpenAI


class VLM:
    def __init__(self, base_url, api_key, model_name):
        self.base_url = base_url
        self.api_key = api_key
        self.model_name = model_name
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def request(
        self, prompt, base64_image, sampling_params={}, model_name=None
    ):
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": f"data:image/jpeg;base64,{base64_image}",
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ]

        response = self.client.chat.completions.create(
            model=model_name if model_name else self.model_name,
            messages=messages,
            **sampling_params,
        )
        return response
