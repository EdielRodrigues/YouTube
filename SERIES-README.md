# Correção exclusiva do catálogo de séries

Substitua `app.py` no repositório GitHub conectado ao Render. A rota `/playlist.m3u` não foi modificada; filmes e TV continuam como antes.

Nova rota: `/series.json`. Ela retorna uma capa por produção identificada, temporadas somente quando indicadas nos títulos e episódios numerados. Vídeos sem número de episódio ficam em `extras`, sem inventar que são episódios completos. O canal dedicado Cruel Istambul é identificado como uma produção; SBT Novelas, quando configurado, fica como conteúdo não classificado até haver metadados de cada novela.

**Importante:** a tela atual do aplicativo Android lê a M3U antiga. Para mostrar uma capa única e uma tela de episódios, o código Android precisará consumir `/series.json`. Esta atualização no Render sozinha NÃO muda a apresentação do aplicativo.

Sem acesso aos metadados reais dos 363 vídeos, não é possível garantir que sejam episódios completos ou identificar todas as temporadas. Esta versão prioriza não misturar nem inventar episódios.
