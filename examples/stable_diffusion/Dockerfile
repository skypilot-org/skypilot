# Note: this file is adapted from https://github.com/AbdBarho/stable-diffusion-webui-docker/blob/master/build/Dockerfile 
# <<EOF heredoc was removed from RUN git clone https://github.com/hlky/sd-enable-textual-inversion.git line
# This was to fix an error when the instance parses the Dockerfile.

# syntax=docker/dockerfile:1

FROM continuumio/miniconda3:4.12.0


RUN conda install python=3.8.5 && conda clean -a -y
RUN conda install pytorch==1.11.0 torchvision==0.12.0 cudatoolkit=11.3 -c pytorch && conda clean -a -y
RUN git clone https://github.com/hlky/stable-diffusion.git && cd stable-diffusion && git reset --hard ff8c2d0b709f1e4180fb19fa5c27ec28c414cedd
RUN conda env update --file stable-diffusion/environment.yaml --name base && conda clean -a -y

# fonts for generating the grid
RUN apt-get update && apt install fonts-dejavu-core rsync -y && apt-get clean

# Note: don't update the sha of previous versions because the install will take forever
# instead, update the repo state in a later step
RUN cd stable-diffusion && git pull && git reset --hard c5b2c86f1479dec75b0e92dd37f9357a68594bda && \
  conda env update --file environment.yaml --name base && conda clean -a -y

# Textual-inversion:
RUN git clone https://github.com/hlky/sd-enable-textual-inversion.git && \
  cd /sd-enable-textual-inversion && git reset --hard 08f9b5046552d17cf7327b30a98410222741b070 && \
  rsync -a /sd-enable-textual-inversion/ /stable-diffusion/

# add info
COPY info.py /info.py
RUN  python /info.py /stable-diffusion/frontend/frontend.py

WORKDIR /stable-diffusion
ENV TRANSFORMERS_CACHE=/cache/transformers TORCH_HOME=/cache/torch CLI_ARGS="" \
  GFPGAN_PATH=/stable-diffusion/src/gfpgan/experiments/pretrained_models/GFPGANv1.3.pth \
  RealESRGAN_PATH=/stable-diffusion/src/realesrgan/experiments/pretrained_models/RealESRGAN_x4plus.pth \
  RealESRGAN_ANIME_PATH=/stable-diffusion/src/realesrgan/experiments/pretrained_models/RealESRGAN_x4plus_anime_6B.pth
EXPOSE 7860
CMD \
  for path in "${GFPGAN_PATH}" "${RealESRGAN_PATH}" "${RealESRGAN_ANIME_PATH}"; do \
  name=$(basename "${path}"); \
  base=$(dirname "${path}"); \
  test -f "/models/${name}" && mkdir -p "${base}" && ln -sf "/models/${name}" "${path}" && echo "Mounted ${name}";\
  done;\
  # force facexlib cache
  mkdir -p /cache/weights/ && rm -rf /stable-diffusion/src/facexlib/facexlib/weights && \
  ln -sf  /cache/weights/ /stable-diffusion/src/facexlib/facexlib/ && \
  # run, -u to not buffer stdout / stderr
  python3 -u scripts/webui.py --outdir /output --ckpt /models/model.ckpt --save-metadata ${CLI_ARGS}
