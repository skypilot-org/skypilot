import tempfile

import cog
from PIL import Image
from PIL import ImageFilter


class Predictor(cog.BasePredictor):

    def predict(
        self,
        image: cog.Path = cog.Input(description='Input image'),
        blur: float = cog.Input(description='Blur radius', default=5),
    ) -> cog.Path:
        if blur == 0:
            return input
        im = Image.open(str(image))
        im = im.filter(ImageFilter.BoxBlur(blur))
        out_path = cog.Path(tempfile.mkdtemp()) / 'out.png'
        im.save(str(out_path))
        return out_path
