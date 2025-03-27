import os
import yaml
import pickle
import logging
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from PIL import Image, ImageTk
import threading
from time import time, sleep
from datetime import datetime
import pandas as pd
import requests

# Configurações globais
CONFIG_FILE = 'config.yaml'
CHECKPOINT_FILE = 'fipe_checkpoint.pkl'
logger = logging.getLogger(__name__)

class RateLimiter:
    def __init__(self, capacity=5, refill_rate=1):
        self.capacity = capacity
        self.tokens = capacity
        self.refill_rate = refill_rate
        self.last_refill = time()

    def acquire(self):
        now = time()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        sleep_time = (1 - self.tokens) / self.refill_rate
        sleep(sleep_time)
        return self.acquire()

class FipeSyncCrawler:
    def __init__(self, gui_callback=None):
        self.config = None
        self.session = requests.Session()
        self.rate_limiter = None
        self.processed = set()
        self.current_table = None
        self.gui_callback = gui_callback
        self.load_config()

    def load_config(self):
        with open(CONFIG_FILE, encoding='utf-8') as f:
            self.config = yaml.safe_load(f)
        self.headers = {
            'User-Agent': self.config['user_agents'][0],
            **self.config['default_headers']
        }
        self.session.headers.update(self.headers)
        self.urls = self.config['api_endpoints']
        self.tipos = self.config['vehicle_types']
        self.combustiveis = self.config['fuel_types']
        self.meses = self.config['month_mapping']
        self.rate_limiter = RateLimiter(
            capacity=self.config.get('rate_limit_capacity', 5),
            refill_rate=self.config.get('rate_limit_refill', 1)
        )
        self.processed = self.load_checkpoint()

    def save_checkpoint(self, force=False):
        if force or len(self.processed) % 50 == 0:
            state = {
                'processed_vehicles': self.processed,
                'current_table': self.current_table,
                'timestamp': datetime.now().isoformat()
            }
            with open(CHECKPOINT_FILE, 'wb') as f:
                pickle.dump(state, f)
            logger.info("Checkpoint salvo com sucesso.")

    def load_checkpoint(self):
        try:
            with open(CHECKPOINT_FILE, 'rb') as f:
                state = pickle.load(f)
                self.current_table = state.get('current_table')
                logger.info(f"Checkpoint carregado. Última atualização: {state.get('timestamp')}")
                return state.get('processed_vehicles', set())
        except (FileNotFoundError, EOFError, KeyError) as e:
            logger.warning(f"Checkpoint não encontrado ou corrompido: {str(e)}")
            return set()

    def http_post(self, url_key, params, retry=3):
        for attempt in range(retry + 1):
            try:
                self.rate_limiter.acquire()
                response = self.session.post(
                    self.urls[url_key],
                    data=params,
                    timeout=self.config.get('timeout', 20)
                )
                if response.status_code == 429:
                    retry_after = int(response.headers.get('Retry-After', 60))
                    logger.warning(f"Rate limit atingido. Tentando novamente em {retry_after}s")
                    sleep(retry_after)
                    continue
                response.raise_for_status()
                return response.json()
            except requests.RequestException as e:
                logger.error(f"Falha na requisição: {str(e)}")
                if attempt < retry:
                    logger.warning(f"Tentativa {attempt + 1} falhou. Tentando novamente...")
                    sleep(1)
                else:
                    return None

    def extract_tabelas(self):
        tabelas = self.http_post('tabelas', {}) or []
        return [
            {
                'id': tbl.get('Codigo'),
                'ano': tbl['Mes'].split('/')[1].strip(),
                'mes_num': self.meses.get(tbl['Mes'].split('/')[0].lower().strip(), '00'),
                'mes_nome': tbl['Mes'].split('/')[0].strip().capitalize()
            }
            for tbl in tabelas if 'Mes' in tbl and int(tbl['Mes'].split('/')[1].strip()) <= 2024
        ]

    def get_marcas(self, tabela_id, tipo=1):
        params = {
            'codigoTabelaReferencia': tabela_id,
            'codigoTipoVeiculo': tipo
        }
        return self.http_post('marcas', params) or []

    def get_modelos(self, tabela_id, tipo=1, marca_id=None):
        params = {
            'codigoTipoVeiculo': tipo,
            'codigoTabelaReferencia': tabela_id,
            'codigoMarca': marca_id
        }
        response = self.http_post('modelos', params)
        return response['Modelos'] if response else []

    def get_ano_modelos(self, tabela_id, tipo=1, marca_id=None, modelo_id=None):
        params = {
            'codigoTipoVeiculo': tipo,
            'codigoTabelaReferencia': tabela_id,
            'codigoMarca': marca_id,
            'codigoModelo': modelo_id
        }
        return self.http_post('ano_modelos', params) or []

    def get_veiculo(self, tabela_id, tipo=1, marca_id=None, modelo_id=None, combustivel=1, ano=None):
        params = {
            'codigoTipoVeiculo': tipo,
            'codigoTabelaReferencia': tabela_id,
            'codigoMarca': marca_id,
            'codigoModelo': modelo_id,
            'codigoTipoCombustivel': combustivel,
            'anoModelo': ano,
            'tipoVeiculo': self.tipos[tipo],
            'tipoConsulta': 'tradicional'
        }
        return self.http_post('veiculo', params)

    def extract_veiculo_data(self, veiculo):
        if not veiculo:
            return None
        try:
            valor = veiculo.get('Valor', 'R$ 0').replace('R$ ', '').replace('.', '').replace(',', '.').strip()
            valor = float(valor) if valor else 0.0
        except ValueError:
            valor = 0.0
        mes_ref = veiculo.get('MesReferencia', '').split()
        mes = self.meses.get(mes_ref[0].lower(), '') if len(mes_ref) > 0 else ''
        ano_ref = mes_ref[2] if len(mes_ref) > 2 else ''
        return {
            'tabela_id': veiculo.get('CodigoTabelaReferencia'),
            'anoref': ano_ref,
            'mesref': mes,
            'tipo': self.tipos.get(veiculo.get('CodigoTipoVeiculo'), 'desconhecido'),
            'fipe_cod': veiculo.get('CodigoFipe'),
            'marca': veiculo.get('Marca', 'N/A'),
            'modelo': veiculo.get('Modelo', 'N/A'),
            'anomod': veiculo.get('AnoModelo', 0),
            'comb_cod': veiculo.get('CodigoTipoCombustivel', 'N/A'),
            'comb_sigla': veiculo.get('SiglaCombustivel', 'N/A'),
            'comb': self.combustiveis.get(veiculo.get('CodigoTipoCombustivel'), 'Desconhecido'),
            'valor': valor,
            'consulta': datetime.now().isoformat()
        }

    def process_vehicle(self, tabela_id, tipo, marca, modelo, ano):
        vehicle_key = f"{tabela_id}-{tipo}-{marca['Value']}-{modelo['Value']}-{ano['Value']}"
        if vehicle_key in self.processed:
            return None
        try:
            cod, combustivel = ano['Value'].split('-')
            if combustivel != '1':  # Apenas gasolina
                return None
        except ValueError:
            return None
        veiculo = self.get_veiculo(tabela_id, tipo, marca['Value'], modelo['Value'], combustivel, cod)
        if not veiculo:
            return None
        self.processed.add(vehicle_key)
        self.save_checkpoint()
        data = self.extract_veiculo_data(veiculo)
        if self.gui_callback and data:
            self.gui_callback('save_vehicle', data)
            self.gui_callback('update_current_vehicle', marca['Label'], modelo['Label'], ano['Value'])
        return data

    def get_veiculos_por_tabela(self, tabela_id, tipos=[1]):
        results = []
        for tipo in tipos:
            marcas = self.get_marcas(tabela_id, tipo)
            if self.gui_callback:
                self.gui_callback('update_progress', 'marcas', len(marcas), 0)
            for i, marca in enumerate(marcas):
                modelos = self.get_modelos(tabela_id, tipo, marca['Value'])
                if self.gui_callback:
                    self.gui_callback('update_progress', 'modelos', len(modelos), 0)
                for j, modelo in enumerate(modelos):
                    anos = self.get_ano_modelos(tabela_id, tipo, marca['Value'], modelo['Value'])
                    if self.gui_callback:
                        self.gui_callback('update_progress', 'anos', len(anos), 0)
                    for k, ano in enumerate(anos):
                        result = self.process_vehicle(tabela_id, tipo, marca, modelo, ano)
                        if result:
                            results.append(result)
                        if self.gui_callback:
                            self.gui_callback('update_progress', 'veiculos', len(results), 0)
        return results


class FipeGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.tables = []
        self.csv_filename = None
        self.excel_filename = None
        self.headers = [
            'tabela_id', 'anoref', 'mesref', 'tipo', 'fipe_cod',
            'marca', 'modelo', 'anomod', 'comb_cod', 'comb_sigla',
            'comb', 'valor', 'consulta'
        ]
        self.title("FIPE Crawler GUI - Todos os Carros a Gasolina até 2024")
        self.geometry("1200x800")
        self.configure(bg='#f0f0f0')
        self.crawler = None
        self.running = False
        self.veiculos = []
        self.progress_bars = {}
        self.setup_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, lambda: self.update_log("Aplicativo inicializado com sucesso!", 'info'))

    def setup_ui(self):
        self.setup_header()
        self.setup_progress_section()
        self.setup_log_section()
        self.setup_control_buttons()

    def setup_header(self):
        header_frame = ttk.Frame(self)
        header_frame.pack(pady=10, fill='x')
        try:
            img = Image.open('fipe_logo.png').resize((100, 100))
            self.logo = ImageTk.PhotoImage(img)
            ttk.Label(header_frame, image=self.logo).pack(side='left')
        except FileNotFoundError:
            pass
        ttk.Label(header_frame, text="Coletor de Dados FIPE - Todos os Carros a Gasolina até 2024",
                  font=('Helvetica', 16, 'bold'), foreground='#2c3e50').pack(pady=10)

    def setup_progress_section(self):
        progress_frame = ttk.LabelFrame(self, text="Progresso")
        progress_frame.pack(pady=10, padx=20, fill='x')
        stages = ["tabelas", "marcas", "modelos", "anos", "veiculos"]
        for i, stage in enumerate(stages):
            frame = ttk.Frame(progress_frame)
            frame.grid(row=i, column=0, sticky='ew', pady=2)
            ttk.Label(frame, text=f"{stage.capitalize()}:").pack(side='left')
            bar = ttk.Progressbar(frame, orient='horizontal', length=300, mode='determinate')
            bar.pack(side='left', expand=True)
            label = ttk.Label(frame, text="0/0")
            label.pack(side='left', padx=5)
            self.progress_bars[stage] = {'bar': bar, 'label': label}
        self.current_vehicle_label = ttk.Label(progress_frame, text="Veículo atual: ")
        self.current_vehicle_label.grid(row=len(stages), column=0, sticky='w', pady=2)

    def setup_log_section(self):
        log_frame = ttk.LabelFrame(self, text="Logs")
        log_frame.pack(pady=10, padx=20, fill='both', expand=True)
        self.log_area = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD)
        self.log_area.pack(fill='both', expand=True)

    def setup_control_buttons(self):
        control_frame = ttk.Frame(self)
        control_frame.pack(pady=10)
        self.start_btn = ttk.Button(control_frame, text="Iniciar", command=self.start_crawler)
        self.start_btn.pack(side='left', padx=5)
        ttk.Button(control_frame, text="Exportar CSV", command=self.export_csv).pack(side='left', padx=5)
        ttk.Button(control_frame, text="Exportar Excel", command=self.export_excel).pack(side='left', padx=5)
        ttk.Button(control_frame, text="Parar", command=self.stop_crawler).pack(side='left', padx=5)

    def update_progress(self, stage, current, total):
        if stage not in self.progress_bars:
            self.update_log(f"Estágio desconhecido: {stage}", 'error')
            return
        bar = self.progress_bars[stage]['bar']
        label = self.progress_bars[stage]['label']
        bar['value'] = (current / total) * 100 if total > 0 else 0
        label['text'] = f"{current}/{total}"
        self.update_idletasks()

    def update_current_vehicle(self, marca, modelo, ano):
        self.current_vehicle_label['text'] = f"Veículo atual: {marca} {modelo} ({ano})"
        self.update_log(f"Processando veículo: {marca} {modelo} ({ano})", 'info')

    def update_log(self, message, level='info'):
        tag = level
        color = {'error': 'red', 'warning': 'orange', 'info': 'black', 'success': 'green'}.get(level, 'black')
        self.log_area.configure(state='normal')
        self.log_area.insert(tk.END, f"{message}\n", tag)
        self.log_area.tag_config(tag, foreground=color)
        self.log_area.configure(state='disabled')
        self.log_area.see(tk.END)

    def start_crawler(self):
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.csv_filename = f"FIPE_carros_gasolina_ate_2024_{timestamp}.csv"
        self.excel_filename = f"FIPE_carros_gasolina_ate_2024_{timestamp}.xlsx"
        pd.DataFrame(columns=self.headers).to_csv(self.csv_filename, index=False)
        self.start_btn['state'] = 'disabled'
        self.running = True
        self.update_log("Iniciando coleta de dados para todas as tabelas até 2024...")
        threading.Thread(target=self.run_sync).start()

    def run_sync(self):
        crawler = FipeSyncCrawler(self.gui_callback)
        self.tables = crawler.extract_tabelas()
        total_tables = len(self.tables)
        self.update_progress('tabelas', 0, total_tables)
        try:
            for i, table in enumerate(self.tables):
                if not self.running:
                    self.update_log("Coleta interrompida pelo usuário!", 'warning')
                    break
                tabela_id = int(table['id'])
                self.update_log(f"Processando tabela {table['mes_nome']}/{table['ano']} (ID: {tabela_id})")
                veiculos = crawler.get_veiculos_por_tabela(tabela_id)
                self.veiculos.extend(veiculos)
                self.update_progress('tabelas', i + 1, total_tables)
            self.update_log(f"Coleta concluída! {len(self.veiculos)} veículos coletados.", 'success')
        except Exception as e:
            self.update_log(f"Erro: {str(e)}", 'error')
        finally:
            self.start_btn.configure(state='normal')
            self.running = False

    def stop_crawler(self):
        if self.running:
            self.running = False
            self.update_log("Coleta será interrompida após a tabela atual!", 'warning')

    def export_csv(self):
        if not os.path.exists(self.csv_filename):
            messagebox.showwarning("Aviso", "Nenhum dado para exportar!")
            return
        df = pd.read_csv(self.csv_filename)
        df.to_csv(self.csv_filename, index=False)
        self.update_log(f"Dados exportados para {self.csv_filename}", 'success')

    def export_excel(self):
        if not os.path.exists(self.csv_filename):
            messagebox.showwarning("Aviso", "Nenhum dado para exportar!")
            return
        df = pd.read_csv(self.csv_filename)
        df.to_excel(self.excel_filename, index=False)
        self.update_log(f"Dados exportados para {self.excel_filename}", 'success')

    def gui_callback(self, action, *args):
        if action == 'update_progress':
            self.after(0, lambda: self.update_progress(*args))
        elif action == 'update_log':
            self.after(0, lambda: self.update_log(*args))
        elif action == 'save_vehicle':
            self.save_vehicle_data(args[0])
        elif action == 'update_current_vehicle':
            self.after(0, lambda: self.update_current_vehicle(*args))

    def save_vehicle_data(self, data):
        try:
            pd.DataFrame([data]).to_csv(self.csv_filename, mode='a', header=False, index=False)
            if len(self.veiculos) % 50 == 0:
                df = pd.read_csv(self.csv_filename)
                df.to_excel(self.excel_filename, index=False)
        except Exception as e:
            self.update_log(f"Erro ao salvar dados: {str(e)}", 'error')

    def on_close(self):
        if self.running:
            if messagebox.askokcancel("Sair", "A coleta está em andamento. Deseja realmente sair?"):
                self.stop_crawler()
                self.destroy()
        else:
            self.destroy()


if __name__ == "__main__":
    app = FipeGUI()
    app.mainloop()
