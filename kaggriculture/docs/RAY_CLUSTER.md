# Ray privado para a #43

Ray apenas transporta lotes. Dentro de cada task, `arena.parallel.matches` ainda cria um
filho descartável por partida e o pai desse filho impõe o deadline. O head é o único
processo que admite seeds e escreve SQLite. O runtime exclui `seed_registry.json`, marca a
task e seus filhos como `ray-worker` e recusa no código tanto `admit_run` quanto abrir um
`JobStore` nesse papel. Jobs remotos com `replay` também são recusados: o resultado completo
volta no `BatchResult` e somente o head decide qualquer persistência final.

## Conexão automática dos dois PCs

Não existe link de dashboard para compartilhar. Cada máquina guarda seu papel em
`~/.config/kaggriculture/ray-cluster.json`, e um serviço `systemd --user` inicia e reconecta
o Ray. A configuração é feita uma única vez.

Primeiro confira a rede dentro do WSL:

```bash
python3 scripts/ray_cluster.py doctor
```

`automatic_address_usable: true` significa que existe um IP de LAN/mirrored ou Tailscale
**dentro do WSL**. O controlador recusa o NAT `172.16/12`, que normalmente não é roteável
pelo segundo PC. Tailscale instalado apenas no Windows não fornece um IP que o processo
Ray do WSL consiga anunciar; instale/conecte também dentro dos dois WSLs, ou use WSL
mirrored na mesma LAN.

No PC A, uma vez:

```bash
python3 scripts/ray_cluster.py configure-head
```

Ele instala a dependência distribuída se necessário, cria o serviço, inicia o head sem
dashboard e imprime o comando exato do PC B. Quando Tailscale oferece MagicDNS, o comando
usa esse nome estável em vez de prender o worker ao IP atual. No PC B, cole-o uma vez:

```bash
python3 scripts/ray_cluster.py configure-worker --head IP_OU_MAGIC_DNS_DO_PC_A:6379
```

Depois disso não há botão: `Restart=always` reconecta o worker quando o head ou a rede
voltam. Ao abrir o repositório, agentes de IA executam o comando idempotente abaixo conforme
o `AGENTS.md` da raiz:

```bash
python3 scripts/ray_cluster.py ensure
```

O serviço também supervisiona o `raylet`: se um agent interno do Ray morrer enquanto o
wrapper `ray start --block` continua vivo, o controlador força a unidade a falhar para que
`Restart=always` realmente reconecte o nó. O arquivo da unidade inclui o hash do controlador;
depois de um `git pull`, o primeiro `ensure` detecta a versão nova e reinicia uma única vez.

Diagnóstico e desligamento explícito:

```bash
python3 scripts/ray_cluster.py status
python3 scripts/ray_cluster.py stop
```

## Capacidade efetiva por máquina

Por padrão, cada nó anuncia `CPUs disponíveis - --leave-cpus-free`. Isso é um ponto de
partida seguro, mas dois cores lógicos de máquinas diferentes podem entregar vazões muito
diferentes. Do head, meça os dois PCs automaticamente nos mesmos jobs com uma carga
suficiente para estabilizar a pool:

```bash
.venv/bin/python scripts/calibrate_ray_capacity.py --jobs=256 \
  --workers=1,4,8,max --output=experiments/results/ray-capacity.json
```

O calibrador recusa resultados divergentes, grava jobs/s, CPU, p50 e p95 por hostname e
recomenda separadamente o worker count de maior vazão observada. Use como capacidade
efetiva o menor número de workers que fica próximo dessa melhor vazão sem pressionar RAM
nem tornar o computador inutilizável. Persista essa decisão na própria máquina com
`--num-cpus`; ela substitui a conta automática baseada na reserva:

```bash
# PC A
python3 scripts/ray_cluster.py configure-head --num-cpus=14

# PC B (preserve o endereço do head já configurado)
python3 scripts/ray_cluster.py configure-worker \
  --head IP_OU_MAGIC_DNS_DO_PC_A:6379 --num-cpus=8
```

`python3 scripts/ray_cluster.py status` informa `advertised_cpus` e `capacity_policy`.
Depois da configuração, `ensure` mantém a capacidade escolhida e o Ray agenda os lotes
dinamicamente. A máquina rápida libera slots antes e recebe mais lotes; não existe divisão
fixa por hostname. O batch adaptativo mantém pelo menos oito vezes mais lotes que slots do
cluster, enquanto o transporte mantém apenas uma onda em voo para não pré-atribuir uma
fila longa ao nó lento. `--batch-size` continua disponível para uma medição controlada.

## Preparação manual equivalente

Os dois nós precisam de Python 3.12, do mesmo checkout e do mesmo ambiente:

```bash
bash scripts/setup.sh --distributed
.venv/bin/python scripts/check_environment.py
```

Ray não fornece uma fronteira de segurança entre clientes e cluster. Use uma LAN privada
ou VPN e bloqueie as portas na interface pública. No head, reserve um ou dois CPUs ao
declarar os recursos e mantenha o dashboard preso a localhost:

```bash
.venv/bin/ray start --head --node-ip-address=HEAD_PRIVATE_IP --port=6379 \
  --dashboard-host=127.0.0.1 --num-cpus=CPUS_DISPONIVEIS
```

No segundo PC:

```bash
.venv/bin/ray start --address=HEAD_PRIVATE_IP:6379 \
  --node-ip-address=WORKER_PRIVATE_IP --num-cpus=CPUS_DISPONIVEIS
```

`CPUS_DISPONIVEIS` deve ser `CPUs lógicos - 1` ou `- 2`. O cluster não deve anunciar os
cores reservados: `--leave-cpus-free` no driver local não consegue corrigir recursos que
um worker remoto anunciou incorretamente.

## Gates antes de throughput

Do head, a prova obrigatória executa as mesmas 400 partidas em todos os nós, compara o
resultado relevante serializado exatamente e roda em cada nó um agente que desliga
`setitimer` e trava:

```bash
.venv/bin/python scripts/verify_ray_cluster.py --address=auto \
  --pairs=200 --output=docs/ray-cluster-verification.json
```

O comando recusa cluster de um único hostname, hash de agente diferente, fingerprint do
motor diferente, qualquer diferença exata no resultado e deadline remoto que não mate o
filho. `money` e `opponent_money` são comparados também pelos oito bytes IEEE-754 binary64,
incluindo o sinal de zero; o artefato grava por NodeID os digests desses bytes e do resultado
relevante completo. A prova de release exige pelo menos 200 seeds, cada um executado uma vez
em cada assento; uma amostra menor é recusada. Ao final, ele mata um worker Ray de verdade
na primeira tentativa e exige que o mapper reenvie aquele lote uma única vez. A morte e a
recuperação são fixadas por afinidade e repetidas em cada NodeID vivo; retries implícitos
do Ray continuam desligados.

Somente depois rode o benchmark. Os quatro tamanhos têm papéis diferentes: 32 é smoke,
256 mede o scheduler local, 1024 mede throughput e 8000 representa a busca real.

```bash
.venv/bin/python scripts/benchmark_ray.py --address=auto \
  --jobs=32,256,1024,8000 \
  --verification=docs/ray-cluster-verification.json \
  --output=docs/ray-benchmark.json
```

Antes da medição distribuída, o script roda o lote inteiro pela pool local em **cada** nó,
usando todos os CPUs que esse nó anunciou ao Ray. O menor wall-clock vira a linha de base;
assim não existe a suposição de que o head seja o PC mais rápido. O relatório grava essas
linhas por hostname, jobs/s, speedup, eficiência paralela, p50 e p95 de partida, cauda de
lote e utilização de CPU. No workload representativo de 8 000 jobs, o comando falha se o
cluster não atingir ao menos 1,10× sobre o melhor `forkserver` local; esse limite pode ser
elevado com `--minimum-representative-speedup`. Uma série que inclui 8 000 também recusa
menos de dois hostnames distintos e mudança de membros durante a medição. O benchmark liga
o arquivo de verificação por SHA-256 e recusa schema, commit, hostnames, agentes, digests ou
gates incompatíveis. Checkout sujo também é recusado, e Python, fingerprint do motor e
hashes dos agentes são comparados novamente por hostname; sem `--verification`, uma série curta é apenas smoke e registra
`benchmark_valid: false`, enquanto a série representativa nem começa.

Referências operacionais: [segurança do Ray](https://docs.ray.io/en/latest/ray-security/index.html),
[tolerância a falhas de tasks](https://docs.ray.io/en/latest/ray-core/fault_tolerance/tasks.html)
e [runtime environments](https://docs.ray.io/en/latest/ray-core/handling-dependencies.html).
