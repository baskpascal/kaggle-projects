# Ingestão admitida do corpus público

Fecha a fronteira de confiança da issue #52. Os CSVs do dataset externo são entrada não
confiável; nenhum episódio vira evidência apenas porque está presente no download.

## Metadados

`experiments.corpus_ingest` exige uma revisão contendo `episodes.csv`,
`episode_features.csv`, `stream_hashes.csv`, `per_submission_coverage.csv` e `teams.csv`.
O comando:

```bash
.venv/bin/python -m experiments.corpus_ingest \
  --metadata data/kaggle/georgy \
  --output experiments/results/corpus-ingest
```

produz `library.json` e `panel-observations.json`. A revisão é SHA-256 de um manifesto
ordenado de caminho, tamanho e digest dos cinco arquivos. A segunda execução sobre os
mesmos bytes é um no-op byte-idêntico; conteúdo diferente no mesmo destino é recusado.

A biblioteca elegível contém somente `EPISODE_TYPE_PUBLIC` no engine `1.32.7`. Cada
assento carrega data, submission, time, rating, rank, `lineage_h24`, `lineage_h48`,
`lineage_h136`, `stream_full_hash`, coverage, peso inverso capado e o
`effective_sample_size` do conjunto. Recusas por engine, tipo, join ou coverage são
contadas explicitamente.

## Oponente reconstruído

Metadado elegível ainda não é agente. `experiments.top_panel` lê os replays, materializa
as duas fitas do episódio e reproduz o resultado publicado nos backends `fast` e
`official`. Use `--observations-output` para emitir as observações diretamente dos
manifests gerados.

Cada reprodução admitida registra:

- revisão do dataset, episódio e assento;
- versão e fingerprint do engine;
- hashes do agente empacotado e do stream;
- vencedor e dinheiro final esperado e real;
- instante da verificação.

`eval.panel.admission()` confere esses valores contra o bundle e recusa campo ausente,
hash divergente, engine divergente ou resultado que não reproduza o publicado. O tipo
`episode_reconstruction` pode entrar numa faixa apenas depois dessa conferência.

## Gate do painel

Ingestão não inventa cobertura. A revisão derivada continua `FAIL` se `top10` ou
`rank10_30` estiver vazia, tiver uma única lineage ou concentração excessiva. Os pesos de
coverage e o tamanho amostral efetivo viajam no snapshot; não substituem os pisos
estruturais do painel.
