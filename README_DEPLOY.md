# Deploy no Netlify - Sistema de Gestão de Combustível

## Versão Recomendada do Python
- Python 3.11.16 (evite Python 3.14 até que os wheels do pandas sejam lançados)

## Instalação Local
Para reproduzir o ambiente localmente:
```
pip install -r requirements.txt
```

## Passos para Deploy no Netlify
1. Certifique-se de que o arquivo `runtime.txt` contém `python-3.11.16`
2. Vá em *Site settings → Build & deploy → Clear cache and deploy site*
3. Execute um novo deploy

## Observações
- O pandas==2.2.2 possui wheel pré-compilado para Python 3.11 e 3.12
- Evite compilação C++ forçando o uso de wheels pré-compilados
- Se o build continuar lento, considere remover libs de renderização pesadas (WeasyPrint, ReportLab, Pillow) se não forem essenciais
