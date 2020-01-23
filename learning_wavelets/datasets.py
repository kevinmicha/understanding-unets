from collections.abc import Iterable

import numpy as np
import scipy.ndimage as ndimage
import tensorflow as tf


# normalisation
def normalise(image):
    return (image / 255) - 0.5

# data augmentation
def random_rotate_image(image):
    # in numpy
    image = ndimage.rotate(image, np.random.uniform(-30, 30), reshape=False)
    return image

def tf_random_rotate_image(image):
    # in tf
    im_shape = image.shape
    [image,] = tf.py_function(random_rotate_image, [image], [tf.float32])
    image.set_shape(im_shape)
    return image

# patch selection
def select_patch_in_image_function(patch_size, seed=0):
    def select_patch_in_image(image):
        if patch_size is not None:
            patch = tf.image.random_crop(
                image,
                [patch_size, patch_size, 1],
                seed=seed,
            )
            return patch
        else:
            return image
    return select_patch_in_image

# padding
def pad_for_pool(n_pooling=1):
    def pad(image):
        im_shape = tf.shape(image)[:2]
        to_pad = (tf.dtypes.cast(im_shape / 2**n_pooling, 'int32') + 1) * 2**n_pooling - im_shape
        # the + 1 is necessary because the images have odd shapes
        pad_seq = [(to_pad[0]//2, to_pad[0]//2 + 1), (to_pad[1]//2, to_pad[1]//2 + 1), (0, 0)]
        image_padded = tf.pad(image, pad_seq, 'SYMMETRIC')
        return image_padded
    return pad

# noise
def add_noise_function(noise_std_range, return_noise_level=False, no_noise=False):
    if not isinstance(noise_std_range, Iterable):
        noise_std_range = (noise_std_range, noise_std_range)
    def add_noise(image):
        noise_std = tf.random.uniform(
            (1,),
            minval=noise_std_range[0],
            maxval=noise_std_range[1],
        )
        if no_noise:
            if return_noise_level:
                return image, noise_std/255
            else:
                return image
        else:
            noise = tf.random.normal(shape=tf.shape(image), mean=0.0, stddev=noise_std/255, dtype=tf.float32)
            if return_noise_level:
                return (image + noise), noise_std/255
            else:
                return image + noise
    return add_noise

def exact_recon_helper(image_noisy, image):
    return (image_noisy, image), (image, image)

def im_dataset_div2k(mode='training', batch_size=1, patch_size=256, noise_std=30, exact_recon=False, return_noise_level=False):
    if mode == 'training':
        path = 'DIV2K_train_HR'
    elif mode == 'validation':
        path = 'DIV2K_valid_HR'
    file_ds = tf.data.Dataset.list_files(f'{path}/*/*.png', seed=0)
    file_ds = file_ds.shuffle(800, seed=0)
    image_ds = file_ds.map(
        tf.io.read_file, num_parallel_calls=tf.data.experimental.AUTOTUNE
    ).map(
        tf.image.decode_png, num_parallel_calls=tf.data.experimental.AUTOTUNE
    )
    image_grey_ds = image_ds.map(
        tf.image.rgb_to_grayscale, num_parallel_calls=tf.data.experimental.AUTOTUNE
    ).map(
        normalise, num_parallel_calls=tf.data.experimental.AUTOTUNE
    )
    # image_grey_aug_ds = image_grey_ds.map(tf_random_rotate_image)
    select_patch_in_image = select_patch_in_image_function(patch_size)
    image_patch_ds = image_grey_ds.map(
        select_patch_in_image, num_parallel_calls=tf.data.experimental.AUTOTUNE
    )
    add_noise = add_noise_function(noise_std, return_noise_level=return_noise_level)
    image_noisy_ds = image_patch_ds.map(
        lambda patch: (add_noise(patch), patch),
        num_parallel_calls=tf.data.experimental.AUTOTUNE,
    )
    if exact_recon:
        # TODO: see how to adapt exact recon for the case of noise level included
        image_noisy_ds = image_noisy_ds.map(
            exact_recon_helper,
            num_parallel_calls=tf.data.experimental.AUTOTUNE,
        )
    image_noisy_ds = image_noisy_ds.batch(batch_size).repeat().prefetch(buffer_size=tf.data.experimental.AUTOTUNE)
    return image_noisy_ds

def im_dataset_bsd500(mode='training', batch_size=1, patch_size=256, noise_std=30, exact_recon=False, no_noise=False, return_noise_level=False, n_pooling=None):
    # the training set for bsd500 is test + train
    # the test set (i.e. containing bsd68 images) is val
    if mode == 'training':
        train_path = 'BSR/BSDS500/data/images/train'
        test_path = 'BSR/BSDS500/data/images/test'
        train_file_ds = tf.data.Dataset.list_files(f'{train_path}/*.jpg', seed=0)
        test_file_ds = tf.data.Dataset.list_files(f'{test_path}/*.jpg', seed=0)
        file_ds = train_file_ds.concatenate(test_file_ds)
    elif mode == 'validation':
        val_path = 'BSR/BSDS500/data/images/val'
        file_ds = tf.data.Dataset.list_files(f'{val_path}/*.jpg', seed=0)
    # TODO: refactor with div2k dataset
    file_ds = file_ds.shuffle(800, seed=0)
    image_ds = file_ds.map(
        tf.io.read_file, num_parallel_calls=tf.data.experimental.AUTOTUNE
    ).map(
        tf.image.decode_jpeg, num_parallel_calls=tf.data.experimental.AUTOTUNE
    )
    image_grey_ds = image_ds.map(
        tf.image.rgb_to_grayscale, num_parallel_calls=tf.data.experimental.AUTOTUNE
    ).map(
        normalise, num_parallel_calls=tf.data.experimental.AUTOTUNE
    )
    # image_grey_aug_ds = image_grey_ds.map(tf_random_rotate_image)
    if patch_size is not None:
        select_patch_in_image = select_patch_in_image_function(patch_size)
        image_patch_ds = image_grey_ds.map(
            select_patch_in_image, num_parallel_calls=tf.data.experimental.AUTOTUNE
        )
    elif n_pooling is not None:
        pad = pad_for_pool(n_pooling)
        image_patch_ds = image_grey_ds.map(
            pad, num_parallel_calls=tf.data.experimental.AUTOTUNE
        )
    else:
        image_patch_ds = image_grey_ds
    add_noise = add_noise_function(noise_std, return_noise_level=return_noise_level, no_noise=no_noise)
    image_noisy_ds = image_patch_ds.map(
        lambda patch: (add_noise(patch), patch),
        num_parallel_calls=tf.data.experimental.AUTOTUNE,
    )
    if exact_recon:
        image_noisy_ds = image_noisy_ds.map(
            exact_recon_helper,
            num_parallel_calls=tf.data.experimental.AUTOTUNE,
        )
    image_noisy_ds = image_noisy_ds.batch(batch_size).repeat().prefetch(buffer_size=tf.data.experimental.AUTOTUNE)
    return image_noisy_ds

def im_dataset_bsd68(batch_size=1, patch_size=256, noise_std=30, exact_recon=False, no_noise=False, return_noise_level=False, n_pooling=None):
    # the training set for bsd500 is test + train
    # the test set (i.e. containing bsd68 images) is val
    path = 'BSD68'
    file_ds = tf.data.Dataset.list_files(f'{path}/*.png', seed=0)
    # TODO: refactor with div2k dataset
    file_ds = file_ds.shuffle(100, seed=0)
    image_ds = file_ds.map(
        tf.io.read_file, num_parallel_calls=tf.data.experimental.AUTOTUNE
    ).map(
        tf.image.decode_png, num_parallel_calls=tf.data.experimental.AUTOTUNE
    )
    image_grey_ds = image_ds.map(
        normalise, num_parallel_calls=tf.data.experimental.AUTOTUNE
    )
    # image_grey_aug_ds = image_grey_ds.map(tf_random_rotate_image)
    if patch_size is not None:
        select_patch_in_image = select_patch_in_image_function(patch_size)
        image_patch_ds = image_grey_ds.map(
            select_patch_in_image, num_parallel_calls=tf.data.experimental.AUTOTUNE
        )
    elif n_pooling is not None:
        pad = pad_for_pool(n_pooling)
        image_patch_ds = image_grey_ds.map(
            pad, num_parallel_calls=tf.data.experimental.AUTOTUNE
        )
    else:
        image_patch_ds = image_grey_ds
    add_noise = add_noise_function(noise_std, return_noise_level=return_noise_level, no_noise=no_noise)
    image_noisy_ds = image_patch_ds.map(
        lambda patch: (add_noise(patch), patch),
        num_parallel_calls=tf.data.experimental.AUTOTUNE,
    )
    if exact_recon:
        image_noisy_ds = image_noisy_ds.map(
            exact_recon_helper,
            num_parallel_calls=tf.data.experimental.AUTOTUNE,
        )
    image_noisy_ds = image_noisy_ds.batch(batch_size).repeat().prefetch(buffer_size=tf.data.experimental.AUTOTUNE)
    return image_noisy_ds
