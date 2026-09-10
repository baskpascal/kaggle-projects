# Primeiro solver de blocos — entrega parcial da #39

`experiments.block_solver` gera variantes de um bloco de ações e mede o valor do
estado de chegada pela continuação fixa até o fim da partida. O objetivo de busca
é o menor placar médio por oponente, seguido do placar médio geral. Empates valem
0,5; dinheiro não desempata. Uma variante neutra mantém o titular.

A busca aceita `--resume`. Cada partida concluída entra imediatamente no cache SQLite
content-addressed compartilhado da máquina, e o histórico é atualizado a cada proposta.
A chave cobre hashes dos artefatos, seed/assento/backend, configuração, fingerprint do
engine, perfil de evidência e contrato dos snapshots; caminhos ficam fora, permitindo
reuso entre worktrees. Claims atômicos evitam execução duplicada concorrente.
`--ray-address auto` transporta snapshots esparsos inline no cluster privado.

A ferramenta já funciona, mas **nenhum ganho competitivo foi demonstrado**.
A #39 deve continuar aberta até existir uma biblioteca melhor e uma decisão de
roteamento medida sobre ela.

## Uso

A partir de `kaggriculture/`, com o ambiente virtual ativo:

```bash
python -m experiments.block_solver \
  --source opponents/public/yhay_router_0908/actions.json --tape-index 0 \
  --start 648 --days 3 --proposals 4 --rounds 1 \
  --opponents clock::opponents/public/thomas_t95/main.py,clock::opponents/public/aberatozer_v23/main.py \
  --seeds 1000:1004 --check-seeds 1008:1012 --workers 4 \
  --output experiments/searches/block-solver-terminal-03
```

O diretório precisa ser novo, salvo ao continuar com `--resume`. A fonte aceita uma lista de fitas ou um envelope
`{"tapes": [...]}`. O início precisa coincidir com uma fronteira de três dias,
com duração de três ou seis dias. O bloco final é truncado no turno 719.

A busca modifica apenas mercado: deslocamento de vendas em um turno, quantidade,
prioridade de uma venda, rotação/reversão dos slots em um turno ou no bloco todo.
São edições próprias sobre uma fita existente, não um planejador que inventa
uma temporada do zero. Prefixo, sufixo, ações do fazendeiro e trabalhadores
permanecem fixos; o manifesto identifica fonte, licença quando disponível, hashes
e mutações aceitas. Não muda o agente em `versions/` nem envia ao Kaggle.

## Fronteiras e estado de chegada

Cada jogo executa o prefixo novamente, em um processo novo, usando o interpretador
oficial. O estado observado antes do bloco precisa ser idêntico ao da referência
na mesma seed, assento e adversário. Divergência invalida a avaliação. Isso evita
restaurar incorretamente RNG ou estado interno de um adversário; custa executar
novamente o prefixo em vez de clonar um checkpoint.

`arrival_state` preserva a fazenda inteira, posições individuais, tiles com idade
e atributos, estoque e inventários separados, além do mercado e cidade públicos.
Não usa a impressão agregada de `tape_splice` como certificado de equivalência.
O valor empírico do estado é `continuation_score`: a vitória/derrota/empate após
executar o sufixo fixo contra o mesmo oponente. Caixa e margem são diagnósticos.
Essa estimativa é condicionada ao sufixo, adversário e amostra; não é uma função
universal de valor nem um limite superior do que outro planejamento alcançaria.

O runner ganhou `replay_steps`, para capturar somente as fronteiras pedidas e
opcionalmente o estado inicial. O formato separado
`kaggriculture-lab-snapshots-v1` impede confundir esses snapshots com um replay
completo. A captura comum de replays continua disponível sem alterações de formato.
O solver preserva estados próprios compactos em `arrivals.json`; remove somente
os snapshots transitórios que ele mesmo acabou de produzir.

## Disciplina de busca

- Busca e check aceitam apenas seeds `dev` registradas, com pelo menos duas em
  cada grupo; os grupos precisam ser disjuntos. Validação e holdout são recusados.
- Todos os candidatos usam os mesmos oponentes, seeds e dois assentos. Hashes e
  ambiente são conferidos, e o pareamento exige blocos completos, sem falhas.
- `selection.json` congela a escolha antes de executar o check. O resultado do
  check não provoca seleção alternativa nem nova rodada automática.
- Reutilizar manualmente o check após inspecioná-lo torna-o desenvolvimento visto.
  Não há alegação de validação inédita, nem reserva/consumo de seeds de validação.
- O conjunto de mutações é determinístico com `--search-seed`. Busca local pode
  estacionar em platôs; o empate não autoriza preferir uma margem maior.

Saídas: `plan.json`, candidatos com partidas e estados de chegada, `selection.json`,
`report.json`, a fita em `tapes.json`, e `main.py` com manifesto. O arquivo final
é uma cópia byte a byte do candidato medido no check. `release_status` é sempre
`not_validated`. Interrupção não produz uma alegação de resultado completo.

## Medição em 08/09/2026

O comando acima executou 112 partidas: cinco pernas de busca (referência e quatro
mutações), mais referência e candidato congelado no check, cada uma com 16 jogos.
O tempo foi 55,31 segundos, com outros testes rodando na máquina.

| Medida | Resultado |
|---|---:|
| Pior oponente na busca, antes/depois | 0,75 / 0,75 |
| Placar agregado na busca, antes/depois | 0,75 / 0,75 |
| Mutações aceitas | 0 |
| Delta no check | 0 |

O delta zero é esperado porque a ferramenta manteve a fita original. Não demonstra
que o espaço de busca esteja esgotado. A referência é a **fita 0 fixa** do yhay,
não o roteador completo nem v004; não há inferência de melhora no ranking.

[block-solver-terminal-03.json](block-solver-terminal-03.json) guarda o plano,
histórico, resultados e hashes dos arquivos de evidência. Os jogos completos ficam
no diretório de experimento ignorado pelo Git. Um piloto anterior de 56 jogos
também não encontrou melhoria. Uma execução maior foi interrompida durante a
sessão; não foi usada como experimento concluído nem somada aos 112 jogos.

Próximas etapas da #39: ampliar edições para produção/trabalho, comparar blocos de
seis dias não terminais, aumentar cobertura de desenvolvimento e, só havendo fitas
complementares melhores, ajustar o roteador. Uma nova estratégia precisa depois
passar pelo driver/gate em validação registrada e pelo preflight real.

## Etapa 2: produção e trabalhadores

`--mutation-space production` acrescenta seis hipóteses: preencher `PASS` com
`WATER`, `HARVEST`, `CARE`, `FEED`, `COLLECT_FERTILIZER` ou `FERTILIZE`.
Cada hipótese aplica uma dessas tarefas aos slots explicitamente ociosos do bloco.
Outras propostas trocam duas tarefas estacionárias consecutivas da mesma unidade,
incluindo `PLANT` com seus argumentos originais. Não troca tarefas entre
trabalhadores, não atravessa fronteiras, não altera comandos de movimento e não
modifica mercado. Isso isola a produção da hipótese anterior de ordens de venda.

Os comandos permanecem sujeitos à execução oficial: fertilizar sem fertilizante
ou colher sem uma planta produtiva pode não fazer nada. A busca registra as ações
ineficazes da partida inteira como diagnóstico. Essa contagem não desempata
vitórias. O estado final pode mudar mesmo que os comandos de movimento sejam
idênticos — a garantia é sobre os comandos emitidos, não a trajetória resultante.

Exemplo de bloco de seis dias, começando no turno 144:

```bash
python -m experiments.block_solver --mutation-space production \
  --source opponents/public/yhay_router_0908/actions.json --start 144 --days 6 \
  --proposals 8 --rounds 1 \
  --opponents clock::opponents/public/thomas_t95/main.py,clock::opponents/public/aberatozer_v23/main.py \
  --seeds 1012:1016 --check-seeds 1020:1024 --workers 4 \
  --output experiments/searches/production-144-01
```

O padrão continua sendo `--mutation-space market`. As duas famílias de mutações
ficam separadas para atribuir resultados. A busca também passou a excluir digests
já medidos **antes** de preencher o orçamento de propostas: tentativas repetidas
não roubam vagas da rodada seguinte. Planos novos registram hashes do código do
solver e dos geradores, além do hash da fita de origem.

Os resultados da etapa 2 ficam em [PRODUCTION_SEARCH.md](PRODUCTION_SEARCH.md).

## Etapa 3: trabalho ciente do estado

`--mutation-space opportunity` lê a trajetória observada do titular dentro do bloco — um
`probe`, uma partida com snapshot por turno, repetida em dois contextos — e propõe apenas
trabalho cuja pré-condição do motor já vale no tile sob aquela unidade naquele turno.
As ações sem efeito por perna caem de 1 696–1 800 para 128–384, e 69–99 slots ociosos
viram 7–28 oportunidades compartilhadas.

Nenhuma das 41 propostas melhorou o placar, e uma única `FEED` legal custou 0,25 de vitória
e 4 483 moedas. O que isso decide para a #39 está em
[OPPORTUNITY_SEARCH.md](OPPORTUNITY_SEARCH.md).

## Etapa 4: troca de bloco e compromisso de sufixo

`--mutation-space swap` troca o bloco inteiro pelo mesmo bloco de outra fita da mesma
biblioteca, e `--days full` troca o sufixo inteiro, que é o que um roteador faz ao escolher
uma fita numa fronteira. 21 doadores em nove buscas, zero aceitos, e a auditoria da regra do
turno 144 explica por quê. Resultado e consequências em [BLOCK_SWAP.md](BLOCK_SWAP.md).
