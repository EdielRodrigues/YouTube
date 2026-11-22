# MEU IPTV — Gerador automático de listas M3U

Busca vídeos públicos do canal `@vportuguese` usando a YouTube Data API v3 e gera uma lista **.m3u** com título, miniatura e URL oficial de cada vídeo.

## Publicar no GitHub e Render

1. Crie um repositório no GitHub e envie os arquivos **desta pasta diretamente na raiz** (incluindo `render.yaml`). Não envie sua chave de API.
2. No Render, clique **New > Blueprint** e conecte o repositório.
3. Informe a variável secreta `YOUTUBE_API_KEY` com sua chave da YouTube Data API v3. Não a coloque no código.
4. Aguarde o deploy e abra `https://SEU-SERVICO.onrender.com/health` para verificar o serviço.
5. Importe `https://SEU-SERVICO.onrender.com/playlist.m3u` no MEU IPTV.

## Endereços

- `/playlist.m3u` — playlist M3U automática
- `/catalog.json` — catálogo em JSON
- `/health` — status

## Observações

- A lista inclui vídeos públicos acessíveis na playlist de uploads, até o limite `MAX_VIDEOS` (padrão: 500); não é garantido que sejam exatamente 444.
- Os endereços são links oficiais do YouTube, **não** streams M3U8. A reprodução exige o player incorporado do YouTube que já funciona no seu aplicativo.
- O YouTube pode restringir a incorporação de determinados vídeos. A presença na lista não garante que todos sejam reproduzíveis.
- Atualização do cache: a cada 6 horas enquanto o serviço recebe solicitações.
- Verifique direitos de uso e regras da API e do player do YouTube.
