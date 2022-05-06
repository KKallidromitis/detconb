#-*- coding:utf-8 -*-
import os
import yaml
import torch
import torch.distributed as dist
from pathlib import Path
from trainer.byol_trainer import BYOLTrainer
from utils import logging_util, distributed_utils
import argparse

parser = argparse.ArgumentParser(description='Detcon-BYOL Training')
parser.add_argument("--local_rank", metavar="Local Rank", type=int, default=0, 
                    help="Torch distributed will automatically pass local argument")
parser.add_argument("--cfg", metavar="Config Filename", default="train_imagenet_300", 
                    help="Experiment to run. Default is Imagenet 300 epochs")
                    
def run_task(config):
    logging = logging_util.get_std_logging()
    if config['distributed']:
        world_size = int(os.environ['WORLD_SIZE'])
        rank = int(os.environ['RANK'])
        local_rank = int(os.environ.get('LOCAL_RANK', '0'))        
        config.update({'world_size': world_size, 'rank': rank, 'local_rank': local_rank})

        dist.init_process_group(backend="nccl", world_size=world_size, rank=rank)
        logging.info(f'world_size {world_size}, gpu {local_rank}, rank {rank} init done.')
    else:
        config.update({'world_size': 1, 'rank': 0, 'local_rank': 0})

    trainer = BYOLTrainer(config)
    #rs = '/shared/jacklishufan/04_13_19-46_resnet50_300.pth.tar'
    rs = None
    trainer.resume_model(model_path=rs)
    start_epoch = trainer.start_epoch

    for epoch in range(start_epoch + 1, trainer.total_epochs + 1):
        trainer.train_epoch(epoch, printer=logging.info)
        trainer.save_checkpoint(epoch)

def main():
    args = parser.parse_args()
    cfg = args.cfg if args.cfg[-5:] == '.yaml' else args.cfg + '.yaml'
    config_path = os.path.join(os.getcwd(), 'config', cfg)
    with open(Path(Path(__file__).parent, 'config/train_imagenet_300.yaml'), 'r') as f:
        config = yaml.safe_load(f)

    if args.local_rank==0:
        print("=> Config Details")
        print(config) #For reference in logs
    
    run_task(config)

if __name__ == "__main__":
    main()
