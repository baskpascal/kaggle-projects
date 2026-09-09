# Uma execução interrompida retoma em vez de recomeçar — issue #42

Até aqui o harness materializava a lista de jobs em memória, jogava tudo e só escrevia
resultado no fim, em `eval/reports.write_report`. Uma execução interrompida na partida
6 200 de 8 000 perdia as 6 200. Agora cada partida terminada é persistida quando chega, e
o que falta é a diferença entre o plano e o que o store já tem.

Nada aqui depende de Ray. É a metade da execução distribuída que vale sozinha, num PC só,
e é pré-requisito do resto.

## A identidade de um job

`job_id` é um SHA-256 sobre `candidate, opponent, seed, seat, backend, split` **mais os
dois hashes de agente** — não sobre os caminhos deles. O hash é identidade melhor que
caminho ou commit por duas razões:

- ele percebe um arquivo **editado e não commitado**, que uma checagem de commit deixa
  passar;
- ele viaja em **cada linha de resultado**, então o agregador pode *recusar* um conjunto
  misto em vez de confiar numa checagem feita antes da execução.

Como o id inclui os hashes, trocar o candidato invalida os jobs dele automaticamente em
vez de reaproveitar resultado errado. O commit do git e um flag de árvore suja também são
gravados, mas como diagnóstico, ao lado dos hashes que são a autoridade.

## SQLite, e o que o 9p obrigou a mudar

SQLite e não JSONL: o resume precisa de leitura por chave, `INSERT OR IGNORE` dá
idempotência de graça, e escrita concorrente é segura. JSONL escreve barato mas obriga a
varrer o arquivo inteiro para saber o que falta.

Duas coisas o ambiente impôs, e as duas estão no código com o motivo escrito:

**A conexão é aberta por operação e nunca é mantida através de uma chamada ao executor.**
O `arena/parallel.py` sobe um forkserver, o processo do forkserver herda os descritores
abertos no instante em que sobe, e uma conexão SQLite carregada através de um fork produz
exatamente o `disk I/O error` que este store estava dando. Conectar custa cerca de um
milissegundo contra uma partida que custa setecentos.

**O journal fica em memória.** O checkout mora num mount 9p/DrvFs, onde o ciclo
criar-escrever-apagar do rollback journal a cada commit é instável enquanto uma dúzia de
workers faz o próprio I/O. Medido no mount: `DELETE` 2,21 s para 300 commits, `MEMORY`
0,66 s, e o `MEMORY` some com a rotatividade de arquivo que estava falhando. O custo é
atomicidade contra queda de energia, **não** durabilidade das linhas já commitadas — e é
justamente uma execução interrompida ou morta que este store existe para resgatar. Por
cima disso, uma falha transitória (`disk i/o error`, `database is locked`) é repetida com
backoff; um erro de programação no SQL não é, porque repeti-lo só seria mais lento.

## Taxonomia de retry

| evento | tratamento |
|---|---|
| worker morreu, pool quebrou, processo não iniciou | **retry** |
| agente lançou exceção | resultado de falha |
| agente estourou o deadline | resultado de falha |
| simulação produziu estado inválido | resultado de falha |

Só a primeira classe é repetida, e a regra não é negociável: repetir as outras faria o
harness selecionar agentes que "dão certo se você tentar algumas vezes", que é exatamente
o que o preflight existe para recusar. Depois de uma falha de infraestrutura o que resta é
recalculado **a partir do store**, então nada já terminado é rejogado e nada perdido no
meio do fluxo é pulado.

## O invariante que a distribuição não pode quebrar

`arena/seeds.admit_run` **muta `seed_registry.json`**, que é versionado no git, antes da
primeira partida, consumindo seeds de validação de forma irreversível. Dois nós com
checkouts próprios mutariam cópias diferentes e a mesma validação poderia ser gasta duas
vezes sem ninguém notar.

A regra é: **só o head admite run e muta o registro; um worker nunca toca no registro.**
Hoje isso já vale porque `admit_run` roda no driver e não por partida, e agora está
testado nos dois sentidos — uma execução de validação interrompida deixa a revisão do
registro em 2, e o `--resume` re-declara o mesmo lote, que o `admit_run` readmite pelo
`batch_id` gravado em vez de queimar as seeds outra vez.

## Persistir na ordem de conclusão — issue #56

O store durável só protege o que **chegou** nele, e por um tempo ele não recebia o que já
estava pronto. Os dois níveis do executor recolocavam os resultados na ordem do plano antes
de entregá-los:

- `arena.parallel.matches` segurava linhas até o índice seguinte aparecer;
- `arena.batch.batched_runner` segurava envelopes até o próximo lote da ordem chegar.

Com o deadline padrão de 300 s, **uma única partida pendurada deixava todas as posteriores
na RAM do driver** até ela morrer. Uma linha comum ocupa 116–134 KB, então o buffer era caro
além de frágil: um driver que caísse nessa janela perdia trabalho já computado e o pagava de
novo no `--resume`.

Agora os dois níveis entregam por conclusão. `matches(..., ordered=False)` — exposto como
`arena.parallel.stream`, que é o runner padrão de `run_league` — devolve cada linha no
instante em que ela cai, e `batched_runner` repassa cada envelope assim que ele chega. O
pico de trabalho computado e não persistido passa a ser a janela em voo, não o run inteiro.

Nada disso vale para a evidência. `execute()` grava cada linha por `job_id` — identidade
recomputada do conteúdo da própria linha, nunca por posição — e devolve o conjunto por
`JobStore.rows_in_order(jobs)`, que remonta **a ordem do plano**. Um relatório e seus
digests não podem se mexer porque um nó estava lento; se mexessem, `plan_sha256`, os
digests de linha da comparação e a identidade de um `RunSpec` passariam a ser afirmações
sobre o cluster em vez de sobre as partidas.

Dentro de um envelope a ordem continua sendo a do plano, que é o que
`validate_batch_result` confere. O `run_batch` joga em modo streaming e correlaciona cada
linha com o seu spec pelo **conteúdo** (`candidate`, `opponent`, `seed`, `seat`, `backend`),
recusando um lote que nomeie a mesma partida duas vezes — sem isso, correlacionar sem
posição seria ambíguo.

`ordered=True` continua existindo e continua sendo um contrato, não um acaso: os pareamentos
posicionais de `experiments/` pedem esse modo explicitamente e `tests/test_parallel.py` o
fixa.

## Onde o store fica

Ao lado do relatório, não dentro dele: `<output>.jobs.sqlite3`. O `write_report` cria o
diretório com `exist_ok=False`, então um store morando lá dentro não sobreviveria à
execução interrompida que ele existe para resgatar. Rodar de novo sobre um store existente
é recusado com mensagem; continuar exige `--resume`, explicitamente.

## O que fica para a #43

Os workers **não abrem** o SQLite do head por filesystem compartilhado: eles jogam
partidas e devolvem linhas, e o head persiste. O `execute()` já recebe o executor como
argumento exatamente por isso — trocar `arena.parallel.matches` por um distribuidor não
toca em nada deste documento.
