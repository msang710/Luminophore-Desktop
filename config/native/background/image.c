#include "image.h"
#include <stdio.h>

#include <jpeglib.h>
#include <png.h>
#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define MAX_PIXELS 32000000UL
struct jpeg_errors {
  struct jpeg_error_mgr base;
  jmp_buf jump;
};
static void jpeg_failed(j_common_ptr state) {
  struct jpeg_errors *e = (void *)state->err;
  longjmp(e->jump, 1);
}
void bg_image_free(struct bg_image *im) {
  free(im->pixels);
  memset(im, 0, sizeof(*im));
}
int bg_image_load(const char *path, struct bg_image *im) {
  memset(im, 0, sizeof(*im));
  FILE *file = fopen(path, "rb");
  if (!file)
    return 0;
  unsigned char signature[8];
  size_t length = fread(signature, 1, 8, file);
  rewind(file);
  if (length == 8 && !png_sig_cmp(signature, 0, 8)) {
    fclose(file);
    png_image image;
    memset(&image, 0, sizeof(image));
    image.version = PNG_IMAGE_VERSION;
    if (!png_image_begin_read_from_file(&image, path))
      return 0;
    if (!image.width || !image.height ||
        (size_t)image.width * image.height > MAX_PIXELS) {
      png_image_free(&image);
      return 0;
    }
    image.format = PNG_FORMAT_RGBA;
    im->pixels = malloc(PNG_IMAGE_SIZE(image));
    if (!im->pixels ||
        !png_image_finish_read(&image, NULL, im->pixels, 0, NULL)) {
      bg_image_free(im);
      png_image_free(&image);
      return 0;
    }
    im->width = image.width;
    im->height = image.height;
    png_image_free(&image);
    return 1;
  }
  struct jpeg_decompress_struct state;
  struct jpeg_errors errors;
  memset(&state, 0, sizeof(state));
  state.err = jpeg_std_error(&errors.base);
  errors.base.error_exit = jpeg_failed;
  if (setjmp(errors.jump)) {
    jpeg_destroy_decompress(&state);
    fclose(file);
    bg_image_free(im);
    return 0;
  }
  jpeg_create_decompress(&state);
  jpeg_stdio_src(&state, file);
  jpeg_read_header(&state, TRUE);
  if (!state.image_width || !state.image_height ||
      (size_t)state.image_width * state.image_height > MAX_PIXELS) {
    jpeg_destroy_decompress(&state);
    fclose(file);
    return 0;
  }
  state.out_color_space = JCS_RGB;
  jpeg_start_decompress(&state);
  im->width = state.output_width;
  im->height = state.output_height;
  im->pixels = malloc((size_t)im->width * im->height * 4);
  if (!im->pixels) {
    jpeg_destroy_decompress(&state);
    fclose(file);
    return 0;
  }
  JSAMPARRAY row = (*state.mem->alloc_sarray)((j_common_ptr)&state, JPOOL_IMAGE,
                                              im->width * 3, 1);
  while (state.output_scanline < state.output_height) {
    size_t y = state.output_scanline;
    jpeg_read_scanlines(&state, row, 1);
    for (int x = 0; x < im->width; x++) {
      unsigned char *p = im->pixels + (y * im->width + x) * 4;
      memcpy(p, row[0] + x * 3, 3);
      p[3] = 255;
    }
  }
  jpeg_finish_decompress(&state);
  jpeg_destroy_decompress(&state);
  fclose(file);
  return 1;
}
