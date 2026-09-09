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
voltam.

`Restart=always` só cobre o processo. Duas camadas acima dele derrubam o nó sem que nada
apareça como falha, e `configure-*`/`ensure` cuidam das duas automaticamente:

- **Sessão de login.** Sem `linger`, o gerenciador `systemd --user` — e o nó Ray junto com
  ele — morre quando a última sessão do usuário termina, ou seja, quando o último terminal
  WSL é fechado. O controlador executa `loginctl enable-linger`, que normalmente não pede
  senha; se o polkit recusar, o JSON diz exatamente qual comando com `sudo` falta.
- **A VM do WSL.** Nada no Windows liga a distribuição depois de um reboot. O controlador
  registra a tarefa agendada `KaggricultureWslKeepAlive`, que no logon executa um lançador
  `.vbs` oculto em `%LOCALAPPDATA%` segurando `sleep infinity` dentro do WSL. O `.vbs`
  existe porque `schtasks` chamando `wsl.exe` direto piscaria um console a cada logon.

`python3 scripts/ray_cluster.py status` relata as duas em `persistence` sem alterar nada;
`ensure` é quem aplica. Rode `ensure` uma vez em cada PC após atualizar o checkout. Ao abrir o repositório, agentes de IA executam o comando idempotente abaixo conforme
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
recomenda separadamente o worker count de maior vazão observada.

### Medição de 2026-09-09: os dois PCs querem o mesmo perfil

Varredura de `cpus_per_worker` em 1, 2, 3 e 4, com 500 partidas por host
(`docs/ray-capacity-cpw-sweep.json`):

| host | cpw=1 | cpw=2 | cpw=3 | cpw=4 |
|------|-------|-------|-------|-------|
| DESKTOP-V3A6VJ6 (PC A) | 0,726/s | 1,595/s | 2,144/s | **2,832/s** |
| DESKTOP-DM63QP1 (PC B) | 0,263/s | 0,421/s | 0,583/s | **1,164/s** |

O ótimo é `cpw=4` nas duas máquinas, então **não** existe perfil de capacidade por host a
implementar: um valor serve aos dois. A hipótese que motivou a medição — de que os 3 CPUs
que sobram no PC B em `floor(11/4)=2` slots fossem desperdício recuperável com um `cpw`
menor — está refutada: reduzir `cpw` piora a vazão nos dois PCs.

Duas coisas que a tabela mostra e que continuam em aberto:

- **A curva não chegou ao topo.** As duas máquinas ainda subiam em `cpw=4`, o maior valor
  medido. A pergunta interessante virou `cpw` acima de 4, não abaixo.
- **O PC B é ~3x mais lento por partida**, não apenas menor: p50 de 1,18 s contra 0,39 s do
  PC A em `cpw=1`, com dispersão bem maior. Isso é característica da máquina, não da
  configuração do Ray.

Use como capacidade
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

## Pedir o cluster explicitamente

Distribuir nunca é automático. Sem flag, uma corrida de partidas roda inteira neste host e
o outro PC fica ocioso mesmo com o cluster de pé — isso é o comportamento correto, não um
defeito. `--ray-address` diz apenas onde procurar o head; quem exige o cluster é
`--distributed`:

```bash
.venv/bin/python -m arena.paired --distributed ...
.venv/bin/python -m arena.league --distributed ...
```

`--distributed` recusa qualquer coisa que não sejam pelo menos duas máquinas distintas
respondendo, com o erro nomeando quem atendeu. Não existe fallback silencioso para local:
uma corrida que pediu o cluster e não o encontrou falha, porque o número medido em um host
só não é o número que foi pedido. Os hostnames verificados e os slots de cada um entram em
`distribution.nodes` do run spec — fora do `run_id`, como toda a distribuição, mas dentro do
relatório, para que depois se saiba em que máquinas aquele resultado foi produzido.

Nada disso vale para `pytest`: os testes são um processo local e não passam pelo Ray.

## O que viaja para os workers

O `working_dir` do Ray leva o checkout, menos `DEFAULT_EXCLUDES` em `arena/ray_transport.py`.
`data/` está fora: é o corpus baixado do Kaggle (episódios, leaderboards, dumps de replay),
6,1 GiB neste checkout, e nenhuma partida abre esse diretório. Enquanto ele viajava, o
pacote passava do teto de 512 MiB do Ray e **toda** corrida distribuída morria no
`ray.init`, antes de agendar um lote — o corpus continua no disco do head, onde os scripts
que o analisam leem normalmente. Com a exclusão o pacote fica em ~28 MiB.

`connect` mede o pacote antes do `ray.init` e imprime o tamanho (`ray working_dir package:
27.6MiB`). Se passar do teto, o erro nomeia os maiores diretórios incluídos em vez de virar
um `RuntimeEnvSetupError` opaco. E `REQUIRED_ROOTS` lista o que uma partida remota abre de
fato — `agent/`, `arena/`, `eval/`, `opponents/`, `versions/`, entre outros: excluir
qualquer um deles é recusado na hora, porque essa falha só apareceria lá dentro do match.

A regra ao mexer nos excludes: tire do pacote o que a partida não abre. Não mova dataset
para dentro do pacote para "resolver" o tamanho.

A prova de que isso funciona nos dois PCs está em `ray-package-smoke.json`: pacote de
6231,99 MiB (falha no `ray.init`) para 27,6 MiB, e lotes reais de partidas executados em
`DESKTOP-V3A6VJ6` e `DESKTOP-DM63QP1`, oito linhas em cada.

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

A prova usa a mesma granularidade padrão de quatro CPUs por tarefa, portanto os 400 jogos
de cada host exercitam também o pool local usado no transporte real.

Somente depois rode o benchmark. Os quatro tamanhos têm papéis diferentes: 32 é smoke,
256 mede o scheduler local, 1024 mede throughput e 8000 representa a busca real.

```bash
.venv/bin/python scripts/benchmark_ray.py --address=auto \
  --jobs=32,256,1024,8000 \
  --verification=docs/ray-cluster-verification.json \
  --output=docs/ray-benchmark.json
```

Antes da medição distribuída, o script roda as cargas até 1 024 pela pool local em
**cada** nó, usando todos os CPUs que esse nó anunciou ao Ray. O menor wall-clock identifica
o host de base. A carga de 8 000 repete o baseline somente nesse host já provado mais
rápido; executar novamente 8 000 no host comprovadamente mais lento não acrescenta
evidência de throughput. Assim não existe a suposição de que o head seja o PC mais rápido. O relatório grava essas
linhas por hostname, jobs/s, speedup, eficiência paralela, p50 e p95 de partida, cauda de
lote e utilização de CPU. No workload representativo de 8 000 jobs, o comando falha se o
cluster não atingir ao menos 1,10× sobre o melhor `forkserver` local; esse limite pode ser
elevado com `--minimum-representative-speedup`. Uma série que inclui 8 000 também recusa
menos de dois hostnames distintos e mudança de membros durante a medição. O benchmark liga
o arquivo de verificação por SHA-256 e recusa schema, commit, hostnames, agentes, digests ou
gates incompatíveis. Checkout sujo também é recusado, e Python, fingerprint do motor e
hashes dos agentes são comparados novamente por hostname; sem `--verification`, uma série curta é apenas smoke e registra
`benchmark_valid: false`, enquanto a série representativa nem começa.

Cada tarefa Ray reserva quatro CPUs por padrão e usa quatro filhos isolados de partida.
Isso evita iniciar um worker Ray pesado por CPU. Os slots são calculados por nó e depois
somados: hosts com 15 e 11 CPUs expõem corretamente `floor(15/4) + floor(11/4) = 5`
tarefas, sem inventar um sexto slot que atravessaria máquinas. A fila mantém apenas uma
onda em voo e a repõe conforme as tarefas terminam. O benchmark grava o JSON atomicamente
após cada carga, preservando as medições concluídas se um nó falhar mais tarde.

Ao trocar hardware ou limites anunciados, recalibre essa granularidade com o cluster já
quente. O comando executa os mesmos 512 resultados em cada configuração, exige igualdade
exata e escolhe pela vazão distribuída medida:

```bash
.venv/bin/python scripts/calibrate_ray_granularity.py --cpu-groups=2,4 \
  --output=docs/ray-granularity.json
```

Referências operacionais: [segurança do Ray](https://docs.ray.io/en/latest/ray-security/index.html),
[tolerância a falhas de tasks](https://docs.ray.io/en/latest/ray-core/fault_tolerance/tasks.html)
e [runtime environments](https://docs.ray.io/en/latest/ray-core/handling-dependencies.html).
