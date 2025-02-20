from xai_components.base import OutArg, InArg, InCompArg, Component, xai_component, dynalist
import requests
import base64

@xai_component
class XpressAIRecognizeDocument(Component):
    """A component to send a request to the OCR server for text recognition.

    ##### inPorts:
    - image_path (str): Path to the image file to be recognized.
    - ocr_type (str): Type of OCR to perform. Default: 'format'.
    - ocr_box (str): OCR box information. Default: ''.
    - ocr_color (str): OCR color information. Default: ''.
    - render (bool): Whether to render the OCR result. Default: False.

    ##### outPorts:
    - recognized_text (str): The recognized text from the OCR server in LaTeX if ocr_type is format.
    - rendered_html (str): The rendered HTML if render is set to True.
    """
    image_path: InCompArg[str]
    ocr_type: InArg[str]
    ocr_box: InArg[str]
    ocr_color: InArg[str]
    render: InArg[bool]

    recognized_text: OutArg[str]
    rendered_html: OutArg[str]

    def execute(self, ctx) -> None:
        with open(self.image_path.value, 'rb') as image_file:
            image_base64 = base64.b64encode(image_file.read()).decode('utf-8')

        url = 'https://recognize.public.cloud.xpress.ai/recognize'
        data = {
            'image': image_base64,
            'ocr_type': self.ocr_type.value if self.ocr_type.value is not None else 'format',
            'ocr_box': self.ocr_box.value if self.ocr_box.value is not None else '',
            'ocr_color': self.ocr_color.value if self.ocr_color.value is not None else '',
            'render': self.render.value if self.render.value is not None else False
        }

        response = requests.post(url, json=data)
        response_data = response.json()

        self.recognized_text.value = response_data.get('text', '')

        if self.render:
            self.rendered_html.value = response_data.get('rendered_html', '')

