# FIPE Crawler - Coletor de Dados de Carros a Gasolina até 2024

Código inicialmente criado por https://github.com/h1ghVoltag3/fipe e adaptado para necessidade pessoal.

Este projeto é um **crawler** desenvolvido em Python para coletar automaticamente dados de preços de carros a gasolina da Tabela FIPE (Fundação Instituto de Pesquisas Econômicas) até o ano de 2024. Utilizando a API oficial da FIPE, o script extrai informações detalhadas de todos os modelos disponíveis, salvando os resultados em arquivos CSV e Excel para análise posterior. A interface gráfica (GUI) permite acompanhar o progresso em tempo real e controlar a execução.

## Funcionalidades Principais
- **Coleta Automática**: Itera por todas as tabelas mensais da FIPE até dezembro de 2024, capturando dados de carros a gasolina.
- **Interface Gráfica**: Desenvolvida com `tkinter`, oferece barras de progresso, logs coloridos e controle de início/parada.
- **Gerenciamento de Requisições**: Inclui um `RateLimiter` para evitar bloqueios por excesso de requisições à API.
- **Persistência de Dados**: Salva os dados em CSV e Excel incrementalmente, com checkpoints para retomar a coleta em caso de interrupção.
- **Exportação Simples**: Botões para exportar os dados coletados nos formatos CSV e Excel.

## Requisitos
- Python 3.8+
- Bibliotecas: `requests`, `pandas`, `tkinter`, `PyYAML`, `Pillow`
- Arquivo `config.yaml` configurado com os endpoints da API FIPE (exemplo incluído no repositório).
