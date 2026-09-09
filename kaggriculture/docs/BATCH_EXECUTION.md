# A camada de lote: o que a #43 precisa antes do Ray, medido num PC só

Esta camada constrói a costura usada pelo transporte local e por Ray. A implementação Ray
é opcional e fica inteiramente no harness; a prova de dois nós continua sendo requisito
antes de considerar a #43 entregue.

## Por que lote, e não uma partida por task

Ray reaproveita processos worker entre tasks. Uma partida por task desfaria em silêncio a
garantia que o `arena/parallel.py` existe para dar: um bundle de terceiro roda código em
tempo de import, o `arena/agents.py` consegue *notar* um rebind mas não desfazê-lo, e o
desfazer é a fronteira de processo. `@ray.remote(max_calls=1)` devolveria o isolamento
matando o worker a cada task, mas o startup de worker do Ray é mais pesado que o `spawn`
que acabamos de eliminar — ficaria mais lento do que não distribuir.

Então **o lote é a unidade de distribuição e a partida continua sendo a unidade de
isolamento**. `arena/batch.py` é essa camada, e ela roda hoje sem Ray.

## A costura

```
arena.jobs.execute(jobs, store, split, runner, workers=...)
                                     ^
                       arena.batch.batched_runner(size=32, map_batches=...)
                                                            ^
                                       o único ponto que um transporte Ray substitui
```

`map_batches(batches, workers)` pode devolver envelopes fora de ordem; `batched_runner`
reordena-os antes de emitir linhas. Cada envelope precisa provar `batch_id`, a lista
ordenada de `job_id`, hostname, hashes e quantidade completa. Lote ausente, duplicado ou
substituído é recusado. O head recalcula ainda cada `job_id` a partir do conteúdo retornado
antes de escrever no SQLite, então uma contagem correta com jobs errados também falha.

`worker_budget(cpus_free=1)` é a política de CPU: deixa um ou dois cores para a máquina,
e nunca devolve menos de um worker. Uma execução que deixa o host inutilizável é
interrompida por um humano, e execução interrompida é pior que execução lenta.

## O risco que a issue mandou checar

> `arena/match.deadline()` usa `signal.setitimer`, que só funciona na thread principal de um
> processo POSIX. Se a task rodar fora dela, o código cai silenciosamente no caminho de
> fallback.

Verificado, com teste: **não importa**. Cada partida roda na thread principal do seu próprio
filho, então o timer é armado onde ele funciona, independentemente de quem chamou o lote. E
a barreira real da #44 vive no **pai** de cada partida, não no timer — um bundle que trava é
morto pelo `killpg` mesmo com o timer desarmado. O teste dirige um lote a partir de uma
thread secundária e exige que o travamento morra assim mesmo.

O outro invariante testado é o que autoriza distribuir: **cortar o trabalho em lotes não
muda uma linha sequer**. Tamanhos 1, 3 e 10 produzem placar, dinheiro e dinheiro do rival
idênticos ao da pool direta.

## Medição nesta máquina

16 cores, `forkserver`, 32 partidas `v004` × `thomas_t95`, backend `fast`. O benchmark
recusa publicar throughput se as linhas divergirem entre configurações.

| workers | direta | em lotes de 8 |
|---:|---:|---:|
| 1 | 0,666 s/partida — 1,50/s | 0,629 s/partida — 1,59/s |
| 2 | 0,307 s/partida — 3,26/s | 0,343 s/partida — 2,91/s |
| 4 | 0,163 s/partida — 6,15/s | 0,195 s/partida — 5,13/s |
| 8 | 0,095 s/partida — 10,52/s | 0,130 s/partida — 7,69/s |
| 14 | **0,080 s/partida — 12,43/s** | 0,130 s/partida — 7,67/s |

Nesta repetição, o modo em lotes perde throughput já a partir de 2 workers e chega a 38%
em 14. A razão dominante continua sendo a granularidade: 32 partidas em lotes de 8 são
**quatro** lotes sequenciais que usam no máximo oito dos catorze workers. Esse
smoke não separa cauda de scheduler com confiança e não deve sustentar uma conclusão de
throughput. No transporte distribuído o tamanho adaptativo mantém pelo menos oito ondas
por slot, limitado a 32; com 8 000 partidas e 14 slots, isso produz 250 lotes.

Na série direta, o speedup de 1 para 14 workers é **8,27×**, com eficiência
paralela de 59,1%. Na série em lotes, é **4,83×**, com eficiência de 34,5%. O script
calcula e grava os dois valores por linha, sempre contra o baseline de 1 worker do mesmo
modo, para o texto nunca depender de uma divisão entre séries diferentes. Reprodução:

```bash
python scripts/benchmark_pool.py --games 32 --workers 1,2,4,8,14 \
  --output docs/pool-benchmark.json
```

## Estado da entrega Ray

`arena.ray_transport` implementa `map_batches` com `num_cpus`, desliga retries implícitos
de exceções da aplicação, verifica todos os nós por afinidade e refaz somente lote perdido
por falha de infraestrutura. Workers não recebem o registro de seeds, não podem chamar
`admit_run`, não podem abrir o SQLite autoritativo e recusam jobs com destino de replay;
somente o `BatchResult` completo cruza a fronteira de transporte. `arena.league` expõe
`--ray-address`, `--cpus-per-worker` e batch adaptativo. Cada tarefa Ray reserva quatro
CPUs por padrão e as usa em quatro filhos isolados de partida; o scheduler soma apenas os
grupos completos que cabem em cada máquina. Ray está no extra `distributed` e
não entra no artefato submetido.

Um cluster local de um nó já executou o caminho completo, com dois jobs distintos no store,
hashes e resultados idênticos, e pacote de runtime de 6,7 MiB. Isso prova integração, não
distribuição. A #43 continua aberta até existirem os dois artefatos de evidência descritos
em `RAY_CLUSTER.md`: determinismo do resultado completo e dos bytes binary64 de dinheiro,
deadline em dois hostnames e então o benchmark de
32, 256, 1 024 e 8 000 jobs. O benchmark mede o `forkserver` em cada nó e usa o menor
wall-clock como baseline; 8 000 jobs precisam superar esse baseline em pelo menos 1,10×.
O relatório só é válido quando carrega o SHA-256 da prova A↔B do mesmo commit, dos mesmos
hostnames e dos mesmos agentes.
