# Pipeline híbrido Ray: dois PCs, CPU + GPU

Esta camada é incremental e fica fora do agente submetido. As partidas continuam usando o
motor e o isolamento existentes; o primeiro modelo é apenas um value-head linear para
provar transporte, batching, CUDA, checkpoint e avaliação antes de escolher uma política
ML/RL real.

## Topologia detectada em 2026-09-09

| nó | papel | Ray CPU | Ray GPU | hardware GPU |
|---|---|---:|---:|---|
| `DESKTOP-V3A6VJ6` | head / PC A | 15 | 0 | — |
| `DESKTOP-DM63QP1` | worker / PC B | 11 | 1 | GTX 1060 6GB, sm_61, driver 580.88 |

Total anunciado: **26 CPUs e 1 GPU**. O Ray já detecta a GPU do PC B. PyTorch CUDA fica
no ambiente Linux separado `~/.local/share/kaggriculture/ml-venv`, exclusivo desse nó; a
`.venv` usada por Ray e pelas partidas continua mínima. O recurso `GPU: 1` sozinho não
prova que CUDA funciona.

## Arquitetura

```text
seeds + agentes
      |
      v
Ray tasks CPU (num_cpus=1, num_gpus=0) nos dois PCs
  partidas isoladas -> rows -> features CPU
      |
      v
replay buffer .npz atômico no head (retomável)
      |
      v
1 Ray actor por GPU (num_cpus=1, num_gpus=1)
  PyTorch + CUDA, trainers independentes, sem DDP
      |
      v
pesos pequenos -> Ray tasks CPU de avaliação
      |
      +---- próximo ciclo acrescenta novas seeds ao buffer
```

CPU é o lugar de simulação, geração de partidas/seeds, extração de features e avaliação.
GPU é reservada exclusivamente pelo actor de treino. Com GPUs futuras, o padrão é criar um
trainer independente por GPU e comparar na avaliação CPU; não há coletivas ou sincronização
multi-GPU.

## Instalação e configuração

No PC A (head, sem GPU):

```bash
bash scripts/setup.sh --distributed
python3 scripts/ray_cluster.py configure-head --num-cpus=15 --num-gpus=0
```

No PC B (worker com a GPU dedicada):

```bash
bash scripts/setup.sh --distributed --ml
python3 scripts/ray_cluster.py configure-worker \
  --head desktop-v3a6vj6.tail0eadc3.ts.net:6379 --num-cpus=11 --num-gpus=1
```

`--ml` cria `~/.local/share/kaggriculture/ml-venv`, instala ali o lock CUDA e registra seu Python em
`~/.config/kaggriculture/ml-python`. O actor GPU carrega apenas os `site-packages` desse
ambiente; tasks de partidas não importam PyTorch. O lock usa `torch==2.9.1+cu126` do
índice oficial CUDA 12.6. Essa linha mantém suporte ao Pascal da GTX 1060; não troque para
CUDA 13 nessa placa.

Depois de atualizar qualquer checkout, em ambos os PCs:

```bash
python3 scripts/ray_cluster.py ensure
```

## Treino/smoke distribuído

Execute no PC A:

```bash
.venv/bin/python -m experiments.hybrid_train \
  --address=auto \
  --candidate=versions/v006/main.py \
  --opponent=opponents/public/thomas_t95/main.py \
  --cycles=1 --seeds-per-cycle=32 \
  --cpus-per-simulation=1 --simulation-batch-size=2 \
  --epochs=20 --require-all-nodes \
  --buffer=experiments/results/hybrid-replay.npz \
  --output=experiments/results/hybrid-training.json
```

`--require-all-nodes` fixa o primeiro batch em cada nó e serve para smoke. Com pelo menos
26 batches, uma CPU por task permite ocupar os 15 + 11 slots sem a fragmentação das antigas
tasks de quatro CPUs. Em corridas maiores, retire a flag: a fila dinâmica deixa o Ray
entregar mais batches ao nó que libera CPU primeiro. O buffer é reaberto e ampliado em nova execução; gravação atômica impede um
processo interrompido de deixar um arquivo parcial.

## Como confirmar uso real

```bash
.venv/bin/ray status
python3 scripts/ray_cluster.py status
python3 -m json.tool experiments/results/hybrid-training.json
```

O relatório deve mostrar:

- dois hostnames em `inventory` e em `cycles[].games_by_hostname` no smoke;
- `ray_cpus` 15 e 11;
- `gpu_probe.cuda_available: true`, `device_name` e `device_capability: [6, 1]`;
- `gpu_ids: ["0"]` e `cuda_visible_devices: "0"` nos estágios `gpu-*`;
- estágios `cpu-*` com `num_gpus: 0`;
- `cuda_peak_memory_bytes > 0` no treino.

Cada task/actor também imprime uma linha `[kaggriculture-resource]` com estágio, hostname,
PID, CPUs/GPU reservadas e IDs atribuídos pelo Ray. Para observar a placa ao vivo no WSL do
PC B:

```bash
watch -n 1 /usr/lib/wsl/lib/nvidia-smi
```

## Smoke medido em 2026-09-09

O smoke de oito partidas com `--require-all-nodes` terminou em 20,19 s. Cada PC executou
quatro partidas. O lote do PC A levou 2,50 s (10,5% de CPU global observada) e o do PC B
10,71 s (33,1%); portanto, neste workload pequeno, simulação no PC B foi o gargalo e ficou
aproximadamente 4,3x mais lenta. O trainer no PC B executou 200 épocas CUDA em 0,84 s, com
pico de 17.048.576 bytes alocados. A avaliação voltou para CPU no PC A.

O probe e o treino confirmaram `CUDA_VISIBLE_DEVICES=0`, Ray GPU ID `0`, CUDA 12.6,
capability 6.1 e a GTX 1060. A `.venv` principal reportou PyTorch ausente nos dois nós,
como esperado; somente o actor GPU carregou o ambiente ML separado. Durante a preparação,
foi reparada uma instalação incompleta do NumPy 2.5.3 na `.venv` do PC B. O DNS desse WSL
não resolvia o índice do PyTorch, então a instalação inicial foi transferida pontualmente
pelo IP Tailscale privado, sem alterar DNS, MagicDNS ou configuração permanente.

## Limites atuais

- A GTX 1060 tem 6 GB: comece com batches pequenos e um único trainer.
- O replay buffer deste scaffold guarda features de resultado de partida, não observações
  por turno; trocar por trajectories é o próximo passo de ML, sem mudar o scheduler.
- O dataset fica no head. Somente arrays do batch viajam para o actor GPU.
- O modelo não altera `agent/`, `versions/` ou o artefato de submissão.
