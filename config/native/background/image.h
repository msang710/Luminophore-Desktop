#pragma once
#include <stddef.h>
struct bg_image {
  unsigned char *pixels;
  int width, height;
};
int bg_image_load(const char *path, struct bg_image *image);
void bg_image_free(struct bg_image *image);
