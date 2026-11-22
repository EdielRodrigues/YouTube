# MEU IPTV — Render com múltiplos canais

Mantém as mesmas rotas `/playlist.m3u`, `/catalog.json`, `/health`, portanto o endereço já configurado no aplicativo continua igual. Não altere a chave `YOUTUBE_API_KEY` do Render.

## Publicação

Substitua `app.py` no repositório GitHub usado pelo Render. Mantenha `requirements.txt` e `render.yaml`. O Render fará o deploy automático, se estiver configurado para isso. Abra `/playlist.m3u` para iniciar a busca e depois `/health` para verificar os resultados por canal.

## Canais incluídos

O código contém os 13 candidatos pedidos: V Português, NetMovies, Filmes para Assistir, Filmelier TV BR, O2 Play, Swen Family Português, Cinerama Português, SBT Novelas, Cruel Istambul, Record News, SBT News, CNN Brasil e Band Jornalismo.

**Importante:** Nem todos os IDs oficiais foram confirmados. Para esses casos, o programa NÃO inventa um canal: ele informa no `/health` a variável que precisa ser configurada. Adicione a variável no Render com o ID oficial `UC...` ou handle `@...` confirmado:

- `YOUTUBE_CHANNEL_NETMOVIES`
- `YOUTUBE_CHANNEL_FILMELIER`
- `YOUTUBE_CHANNEL_O2PLAY`
- `YOUTUBE_CHANNEL_SWENFAMILY`
- `YOUTUBE_CHANNEL_SBTNOVELAS`
- `YOUTUBE_CHANNEL_CRUELISTAMBUL`

Os demais já possuem ID ou handle candidato no código; confirme em `/health` que resolveram corretamente. Para corrigir qualquer um, use `YOUTUBE_CHANNEL_<CHAVE>` (ex.: `YOUTUBE_CHANNEL_CNNBRASIL`).

## TV ao vivo

Busca apenas lives **ativas** dos canais de notícias. Uma live pode não permitir reprodução incorporada no player do YouTube. A busca de lives usa `search.list` (100 unidades de cota por canal e atualização), portanto 4 canais ao vivo custam cerca de 400 unidades por ciclo. Com cache de 6 horas, isso pode chegar a aproximadamente 1600 unidades/dia, além das consultas aos outros canais. O serviço não usa links HLS privados.

## Notas

- A lista de filmes e novelas inclui uploads públicos, que podem conter trailers e cortes; ainda não há classificação automática de longa-metragem/episódio.
- Um canal com erro não impede os demais de carregar. Se já havia itens desse canal no cache, eles são mantidos temporariamente.
- O total de vídeos pode ultrapassar 500 porque `MAX_VIDEOS` é o limite **por canal**.
- O app pode mostrar tudo na seção Filmes se sua integração Android atual não separar categorias por tipo. Para exibir TV ao vivo e Séries nas abas próprias, será necessária uma alteração posterior no Android.
- Use somente conteúdo autorizado e respeite as condições do player incorporado do YouTube.
