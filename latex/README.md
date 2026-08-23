# Documentação técnica — LaTeX

Fonte da **documentação técnica consolidada** do Sistema de Manutenção
Prescritiva para Máquinas Rotativas (`PMX-DOC-001`). Compila em `main.pdf`.

## Compilar

```powershell
# Windows (recomendado)
.\compilar.ps1              # build completo: pdflatex → biber → pdflatex ×2
.\compilar.ps1 -Rapido      # uma passagem, para iterar na escrita
.\compilar.ps1 -Limpar      # remove os arquivos auxiliares
```

```bash
# Qualquer plataforma
latexmk -pdf main.tex

# Ou manualmente (quatro passagens)
pdflatex main && biber main && pdflatex main && pdflatex main
```

## Requisitos

| Item | Detalhe |
|---|---|
| Distribuição | MiKTeX ou TeX Live, com `pdflatex` e `biber` |
| Motor | **pdfLaTeX puro** — não exige XeLaTeX nem LuaLaTeX |
| Fontes | `roboto` e `roboto-mono` (Roboto e Roboto Mono em Type1/OTF) |
| Diagramas | `tikz` e `pgfplots` — todos vetoriais, sem renderização externa |
| Bibliografia | `biblatex` com estilo `abnt` (ABNT NBR 6023), processada por `biber` |
| Tabelas | `booktabs`, `tabularx`, `ltablex`, `colortbl` |
| Código | `listings` com dialetos para Python, YAML, JSON, TypeScript, Bash e Mermaid |

No MiKTeX, pacotes ausentes são instalados sob demanda. Para instalar
explicitamente o estilo de bibliografia:

```powershell
mpm --install=biblatex-abnt
```

## Estrutura

```
latex/
├── main.tex                 documento mestre (ordem dos capítulos)
├── referencias.bib          bibliografia (formatada em ABNT na saída)
├── compilar.ps1             script de build
├── preambulo/
│   ├── estilo.tex           tipografia, cores, tabelas, código, títulos, rodapé
│   ├── diagramas.tex        biblioteca TikZ (C4, UML, ER, infra, gráficos)
│   └── capa.tex             capa, ficha técnica e histórico de revisões
├── secoes/                  00-siglas … 18-conclusao
├── apendices/               a-mermaid … f-operacao
├── figuras/                 imagens externas (logotipo)
└── build/                   pré-visualizações geradas (não versionado)
```

## Convenções de edição

- **Cores**: usar apenas os tokens `pmx*` definidos em `preambulo/estilo.tex`.
  A paleta é a mesma validada para daltonismo em `src/ui/theme.py`.
- **Código em linha**: `\cd{...}` para identificadores e `\arq{...}` para
  arquivos. Ambos usam `\texttt`+`\textcolor` deliberadamente — `{\ttfamily...}`
  troca a fonte antes de o parágrafo começar e desalinha a primeira linha de
  base nas células de tabela.
- **Blocos de destaque**: `nota`, `atencao`, `decisao`, `limitacao`,
  `destaque`, `especificacao{título}`.
- **Diagramas**: envolver em `\fitwidth{...}` (encolhe só se exceder a largura)
  ou `\fitpage{...}` em páginas em paisagem. Diagramas largos vão dentro de
  `\begin{landscape}`.
- **Identificadores rastreáveis**: `\req{RF-01}`, `\adrr{ADR-09}`,
  `\ucr{UC-01}`.
- **Mermaid**: ao acrescentar um diagrama TikZ, incluir a fonte Mermaid
  equivalente em `apendices/a-mermaid.tex`, referenciando o mesmo número de
  figura.

## Notas de compilação

- O log registra avisos de `Infinite glue shrinkage` nas tabelas que quebram
  entre páginas. São diagnósticos benignos do `longtable` (`ignored error`); a
  saída não é afetada.
- A primeira passagem deixa referências cruzadas e o sumário pendentes. Só após
  a quarta passagem os números de página e as citações estabilizam.
