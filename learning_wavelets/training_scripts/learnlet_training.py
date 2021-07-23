import click
import os
import os.path as op
import tensorflow as tf
import time

try:
    import numpy as np
    import cadmos_lib as cl
    has_cadmos = True
except ImportError:
    has_cadmos = False

from tensorflow.keras.callbacks import TensorBoard, ModelCheckpoint, LearningRateScheduler
from tensorflow.keras.optimizers import Adam

from learning_wavelets.config import LOGS_DIR, CHECKPOINTS_DIR
from learning_wavelets.data.datasets import im_dataset_div2k, im_dataset_bsd500
from learning_wavelets.evaluate import keras_psnr, keras_ssim, center_keras_psnr
from learning_wavelets.keras_utils.normalisation import NormalisationAdjustment
from learning_wavelets.models.learnlet_model import Learnlet

tf.random.set_seed(1)

@click.command()
@click.option(
    'noise_std_train',
    '--ns-train',
    nargs=2,
    default=(0, 55),
    type=float,
    help='The noise standard deviation range for the training set. Defaults to [0, 55]',
)
@click.option(
    'noise_std_val',
    '--ns-val',
    default=30,
    type=float,
    help='The noise standard deviation for the validation set. Defaults to 30',
)
@click.option(
    'n_samples',
    '-n',
    default=None,
    type=int,
    help='The number of samples to use for training. Defaults to None, which means that all samples are used.',
)
@click.option(
    'n_scales',
    '-sc',
    default=5,
    type=int,
    help='The number of scales in the architecture. Defaults to 5',
)
@click.option(
    'source',
    '-s',
    default='bsd500',
    type=click.Choice(['bsd500', 'div2k'], case_sensitive=False),
    help='The dataset you wish to use for training and validation, between bsd500 and div2k. Defaults to bsd500',
)
@click.option(
    'cuda_visible_devices',
    '-gpus',
    '--cuda-visible-devices',
    default='0123',
    type=str,
    help='The visible GPU devices. Defaults to 0123',
)
@click.option(
    'denoising_activation',
    '-da',
    '--denoising-activation',
    default='dynamic_soft_thresholding',
    type=click.Choice([
        'dynamic_soft_thresholding',
        'dynamic_hard_thresholding',
        'dynamic_soft_thresholding_per_filter',
        'cheeky_dynamic_hard_thresholding'
    ], case_sensitive=False),
    help='The denoising activation to use. Defaults to dynamic_soft_thresholding',
)
@click.option(
    'n_filters',
    '-nf',
    '--n-filters',
    default=256,
    type=int,
    help='The number of filters in the learnlets. Defaults to 256.',
)
@click.option(
    'kernel_sizes',
    '--k-sizes',
    nargs=2,
    default=(11, 13),
    type=int,
    help='The analysis and synthesis kernel sizes. Defaults to [11, 13]',
)
@click.option(
    'decreasing_noise_level',
    '--decr-n-lvl',
    is_flag=True,
    help='Set if you want the noise level distribution to be non uniform, skewed towards low value.',
)
@click.option(
    'exact_reco',
    '-e',
    is_flag=True,
    help='Set if you want the learnlets to have exact reconstruction.',
)
@click.option(
    'n_reweights',
    '-nr',
    default=1,
    help='The number of reweights. Defaults to 1.',
)

def train_learnlet(
        noise_std_train,
        noise_std_val,
        n_samples,
        n_scales,
        source,
        cuda_visible_devices,
        denoising_activation,
        n_filters,
        kernel_sizes,
        decreasing_noise_level,
        exact_reco,
        n_reweights,
    ):
    os.environ["CUDA_VISIBLE_DEVICES"] = ','.join(cuda_visible_devices)
    # data preparation
    batch_size = 8
    if source == 'bsd500':
        data_func = im_dataset_bsd500
    elif source == 'div2k':
        data_func = im_dataset_div2k
    im_ds_train = data_func(
        mode='training',
        batch_size=batch_size,
        patch_size=256,
        noise_std=noise_std_train,
        return_noise_level=True,
        n_samples=n_samples,
        decreasing_noise_level=decreasing_noise_level,
    )
    im_ds_val = data_func(
        mode='validation',
        batch_size=batch_size,
        patch_size=256,
        noise_std=noise_std_val,
        return_noise_level=True,
    )
    analysis_kernel_size, synthesis_kernel_size = kernel_sizes
    run_params = {
        'denoising_activation': denoising_activation,
        'learnlet_analysis_kwargs':{
            'n_tiling': n_filters,
            'mixing_details': False,
            'skip_connection': True,
            'kernel_size': analysis_kernel_size,
        },
        'learnlet_synthesis_kwargs': {
          'res': True,
            'kernel_size': synthesis_kernel_size,
        },
        'threshold_kwargs':{
            'noise_std_norm': True,
        },
        'n_scales': n_scales,
        'n_reweights_learn': n_reweights,
        'exact_reconstruction': exact_reco,
        'clip': False,
    }

    n_epochs = 500
    run_id = f'learnlet_exact_recon_{exact_reco}_{n_filters}_{denoising_activation}_{source}_{noise_std_train[0]}_{noise_std_train[1]}_{n_samples}_{int(time.time())}'
    chkpt_path = f'{CHECKPOINTS_DIR}checkpoints/{run_id}' + '-{epoch:02d}.hdf5'
    print(run_id)




    def l_rate_schedule(epoch):
        return max(1e-3 / 2**(epoch//25), 1e-5)
    lrate_cback = LearningRateScheduler(l_rate_schedule)




    chkpt_cback = ModelCheckpoint(chkpt_path, period=n_epochs, save_weights_only=False)
    log_dir = op.join(f'{LOGS_DIR}logs', run_id)
    tboard_cback = TensorBoard(
        log_dir=log_dir,
        histogram_freq=0,
        write_graph=False,
        write_images=False,
        profile_batch=0,
    )
    norm_cback = NormalisationAdjustment(momentum=0.99, n_pooling=5)
    norm_cback.on_train_batch_end = norm_cback.on_batch_end


    n_channels = 1
    # run distributed
    mirrored_strategy = tf.distribute.MirroredStrategy()
    with mirrored_strategy.scope():
        model = Learnlet(**run_params)
        model.compile(
            optimizer=Adam(lr=1e-3),
            loss='mse',
            metrics=[keras_psnr, keras_ssim],
        )
        inputs = [tf.zeros((1, 32, 32, 1)), tf.zeros((1, 1))]
        model(inputs)
    
    # Introducing shearlet filters for the analysis part
    
    if has_cadmos:
        shearlets, shearlets_adj = cl.get_shearlets(512, 512, n_scales + 1) 

        n_shearlets = np.shape(shearlets)[0]
        total_size = np.shape(shearlets)[1]
        n_shearlets_adj = np.shape(shearlets_adj)[0]
        total_size_adj = np.shape(shearlets_adj)[1]

        crop_min = total_size//2 - analysis_kernel_size//2
        crop_max = total_size//2 + analysis_kernel_size//2 + 1
        resized_shearlets = np.zeros((n_scales, analysis_kernel_size, analysis_kernel_size, 1, n_shearlets)) 
        crop_min_adj = total_size_adj//2 - synthesis_kernel_size//2
        crop_max_adj = total_size_adj//2 + synthesis_kernel_size//2 + 1
        resized_shearlets_adj = np.zeros((n_scales, synthesis_kernel_size, synthesis_kernel_size, n_shearlets_adj, 1)) 

        for i in range(n_shearlets):
            resized_shearlets[:,:,:,0,i] = shearlets[i, crop_min:crop_max, crop_min:crop_max] 
        analysis_filters = model.layers[0].get_weights()
        for i in range(n_scales + 1, 2*n_scales + 1):
            analysis_filters[i] = resized_shearlets[i-(n_scales + 1),:,:,:,:]
        model.layers[0].set_weights(analysis_filters)

        for i in range(n_shearlets_adj):
            resized_shearlets_adj[:,:,:,i,0] = shearlets_adj[i, crop_min_adj:crop_max_adj, crop_min_adj:crop_max_adj] 
        synthesis_filters = model.layers[2].get_weights()
        for i in range(n_scales, 2*n_scales):
            synthesis_filters[i] = resized_shearlets_adj[i-n_scales,:,:,:,:]
        model.layers[2].set_weights(synthesis_filters)

    model.fit(
        im_ds_train,
        steps_per_epoch=200,
        epochs=n_epochs,
        validation_data=im_ds_val,
        validation_steps=1,
        verbose=0,
        callbacks=[tboard_cback, chkpt_cback, norm_cback, lrate_cback],
        shuffle=False,
    )

if __name__ == '__main__':
    train_learnlet()
