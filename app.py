
from flask import Flask, render_template, request, redirect, url_for, session, Response, flash, make_response
from datetime import datetime, date
import os
import csv
import io
from database import db, Veiculo, Motorista, Abastecimento, ContratoCombustivel, ContratoCombustivelItem, AditivoContratoCombustivel, User
from werkzeug.security import check_password_hash, generate_password_hash
from weasyprint import HTML
from sqlalchemy import func, desc
from collections import defaultdict

# ----------------------
# Configuração principal
# ----------------------
app = Flask(__name__, instance_relative_config=True)
app.config['SECRET_KEY'] = 'troque-esta-chave-por-uma-segura'

app.config['TEMPLATES_AUTO_RELOAD'] = True

# Define o caminho do banco na pasta instance, ao lado do executável
instance_path = os.path.join(os.getcwd(), 'instance')
os.makedirs(instance_path, exist_ok=True)
DB_PATH = os.path.join(instance_path, "database.db")

# Configuração do banco de dados
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DB_PATH}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

# ----------------------
# Filtros Jinja2
# ----------------------
@app.template_filter('now')
def now(value, format='%d/%m/%Y %H:%M'):
    return datetime.now().strftime(format)

@app.template_filter('currency')
def currency_filter(value):
    """Formata um número como moeda no padrão brasileiro (R$ 1.234,56)."""
    if value is None or value == "":
        return "R$ 0,00"
    try:
        # Formata com separador de milhar (,) e 2 casas decimais
        formatted = f"{float(value):,.2f}"
        # Troca: , → . e . → ,
        return f"R$ {formatted.replace(',', 'X').replace('.', ',').replace('X', '.')}"
    except (ValueError, TypeError, AttributeError):
        return "R$ 0,00"

@app.template_filter('number')
def number_filter(value, decimals=0):
    if value is None or value == "":
        value = 0
    try:
        value = float(value)
        if decimals == 0:
            value = int(round(value))
            return f"{value:,}".replace(",", "X").replace(".", ",").replace("X", ".")
        else:
            formatted = f"{value:,.{decimals}f}"
            return formatted.replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return "0"



@app.template_filter('litros')
def litros_filter(value):
    return f"{number_filter(value, 2)} L"


# ----------------------
# Tipos de Combustível
# ----------------------
TIPOS_COMBUSTIVEL = [
    "Gasolina",
    "Álcool", 
    "Flex",
    "Diesel"
]

# ----------------------
# Função Auxiliar: Cálculo do Relatório
# ----------------------
def calcular_dados_relatorio_contratos():
    """Calcula os dados do relatório de contratos de combustível."""
    contratos = ContratoCombustivel.query.filter_by(ativo=True).order_by(ContratoCombustivel.data_inicio_contrato).all()
    dados_relatorio = []

    total_contratos_ativos = len(contratos)
    total_valor_contratado = 0
    total_valor_consumido = 0
    total_valor_restante = 0

    for contrato in contratos:
        for item in contrato.itens:
            quantidade_contratada = item.quantidade
            valor_total_contratado = item.valor_total

            inicio_contrato = datetime.combine(contrato.data_inicio_contrato, datetime.min.time())
            fim_contrato = datetime.combine(contrato.data_fim_contrato, datetime.max.time())

            abastecimentos_tipo = Abastecimento.query.join(Veiculo).filter(
                Veiculo.combustivel == item.tipo_combustivel,
                Abastecimento.data >= inicio_contrato,
                Abastecimento.data <= fim_contrato
            ).all()

            abastecimentos_contrato = Abastecimento.query.filter(
                Abastecimento.contrato_id == contrato.id,
                Abastecimento.data >= inicio_contrato,
                Abastecimento.data <= fim_contrato
            ).all()

            todos_abastecimentos = list(set(abastecimentos_tipo + abastecimentos_contrato))

            quantidade_consumida = sum(a.litros for a in todos_abastecimentos)
            valor_usado = sum(a.valor_total for a in todos_abastecimentos)

            quantidade_restante = max(0, quantidade_contratada - quantidade_consumida)
            valor_restante = max(0, valor_total_contratado - valor_usado)
            percentual_consumido = (quantidade_consumida / quantidade_contratada * 100) if quantidade_contratada > 0 else 0

            dados_relatorio.append({
                'tipo_combustivel': item.tipo_combustivel,
                'fornecedor': contrato.fornecedor,
                'data_inicio_contrato': contrato.data_inicio_contrato,
                'data_fim_contrato': contrato.data_fim_contrato,
                'quantidade_contratada': quantidade_contratada,
                'valor_total': valor_total_contratado,
                'valor_por_litro': item.valor_por_litro,
                'quantidade_consumida': quantidade_consumida,
                'valor_usado': valor_usado,
                'quantidade_restante': quantidade_restante,
                'valor_restante': valor_restante,
                'percentual_consumido': percentual_consumido
            })

            total_valor_contratado += valor_total_contratado
            total_valor_consumido += valor_usado
            total_valor_restante += valor_restante

    return {
        'dados_relatorio': dados_relatorio,
        'total_contratos_ativos': total_contratos_ativos,
        'total_valor_contratado': total_valor_contratado,
        'total_valor_consumido': total_valor_consumido,
        'total_valor_restante': total_valor_restante
    }

# ----------------------
# Inicialização do banco
# ----------------------
with app.app_context():
    db.create_all()

# ----------------------
# Rotas
# ----------------------

@app.route("/")
def index():
    if "usuario" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))

@app.route("/dashboard")
def dashboard():
    """Dashboard principal com filtros, indicadores e gráficos"""
    if "usuario" not in session:
        return redirect(url_for("login"))

    # Obter parâmetros de filtro
    data_inicio = request.args.get("data_inicio", "")
    data_fim = request.args.get("data_fim", "")
    veiculo_id = request.args.get("veiculo_id", "")
    motorista_id = request.args.get("motorista_id", "")
    combustivel = request.args.get("combustivel", "")
    agrupamento = request.args.get("agrupamento", "dia")  # dia, semana, mes
    setor_filtro = request.args.get("setor", "")


    # Obter tipo e setor do usuário logado
    usuario_tipo = session.get("usuario_tipo")
    usuario_setor = session.get("usuario_setor")

    # Construir query base com joins explícitos
    query = db.session.query(Abastecimento).join(Abastecimento.veiculo).join(Abastecimento.motorista)
    # Filtro de setor: admin pode escolher, usuário comum só vê o próprio setor
    if usuario_tipo == "admin":
        if setor_filtro:
            query = query.filter(Veiculo.tipo == setor_filtro)
    elif usuario_setor:
        query = query.filter(Veiculo.tipo == usuario_setor)

    # Filtros de data
    if data_inicio:
        try:
            data_inicio_obj = datetime.fromisoformat(data_inicio)
            query = query.filter(Abastecimento.data >= data_inicio_obj)
        except (ValueError, TypeError):
            data_inicio = ""
    if data_fim:
        try:
            data_fim_obj = datetime.fromisoformat(data_fim)
            data_fim_obj = data_fim_obj.replace(hour=23, minute=59, second=59)
            query = query.filter(Abastecimento.data <= data_fim_obj)
        except (ValueError, TypeError):
            data_fim = ""

    # Filtros de veículo
    if veiculo_id:
        try:
            query = query.filter(Abastecimento.veiculo_id == int(veiculo_id))
        except (ValueError, TypeError):
            veiculo_id = ""

    # Filtro de motorista
    if motorista_id:
        try:
            query = query.filter(Abastecimento.motorista_id == int(motorista_id))
        except (ValueError, TypeError):
            motorista_id = ""

    # ✅ Filtro de combustível: filtrar pelos veículos com o tipo de combustível selecionado
    if combustivel:
        query = query.filter(Veiculo.combustivel == combustivel)

    # Executar a consulta
    abastecimentos = query.order_by(desc(Abastecimento.data)).all()

    # Indicadores
    total_litros = sum(a.litros for a in abastecimentos) if abastecimentos else 0
    valor_total = sum(a.valor_total for a in abastecimentos) if abastecimentos else 0
    media_litros = total_litros / len(abastecimentos) if abastecimentos else 0
    if usuario_tipo != "admin" and usuario_setor:
        total_veiculos = Veiculo.query.filter(Veiculo.tipo == usuario_setor).count()
    else:
        total_veiculos = Veiculo.query.count()

    # Gráfico: Litros por período (dia, semana, mês)
    litros_por_periodo = defaultdict(float)
    for a in abastecimentos:
        if agrupamento == "semana":
            key = a.data.strftime('%Y-W%U')  # Ano-Semana
        elif agrupamento == "mes":
            key = a.data.strftime('%Y-%m')  # Ano-Mês
        else:
            key = a.data.strftime('%Y-%m-%d')  # Dia
        litros_por_periodo[key] += a.litros
    litros_por_periodo_ordenado = dict(sorted(litros_por_periodo.items()))

    # Gráfico: Top 10 Veículos
    litros_por_veiculo = defaultdict(float)
    for a in abastecimentos:
        placa = a.veiculo.placa if a.veiculo else f"Veículo {a.veiculo_id}"
        litros_por_veiculo[placa] += a.litros
    top_veiculos_items = sorted(litros_por_veiculo.items(), key=lambda x: x[1], reverse=True)[:10]
    litros_por_veiculo_top10 = dict(top_veiculos_items)

    # Gráfico: Top 10 Motoristas
    litros_por_motorista = defaultdict(float)
    for a in abastecimentos:
        nome = a.motorista.nome_completo if a.motorista else f"Motorista {a.motorista_id}"
        litros_por_motorista[nome] += a.litros
    top_motoristas_items = sorted(litros_por_motorista.items(), key=lambda x: x[1], reverse=True)[:10]
    litros_por_motorista_top10 = dict(top_motoristas_items)

    # Gráfico: Litros por combustível
    litros_por_combustivel = defaultdict(float)
    for a in abastecimentos:
        tipo = a.veiculo.combustivel if a.veiculo else "Não informado"
        litros_por_combustivel[tipo] += a.litros

    # Dados para os filtros no template

    # Filtrar veículos e motoristas pelo setor selecionado (admin) ou setor do usuário
    if usuario_tipo == "admin":
        if setor_filtro:
            veiculos = Veiculo.query.filter(Veiculo.tipo == setor_filtro).order_by(Veiculo.placa).all()
            motoristas = Motorista.query.join(Abastecimento).join(Veiculo).filter(Veiculo.tipo == setor_filtro).order_by(Motorista.nome_completo).distinct().all()
        else:
            veiculos = Veiculo.query.order_by(Veiculo.placa).all()
            motoristas = Motorista.query.order_by(Motorista.nome_completo).all()
    elif usuario_setor:
        veiculos = Veiculo.query.filter(Veiculo.tipo == usuario_setor).order_by(Veiculo.placa).all()
        motoristas = Motorista.query.join(Abastecimento).join(Veiculo).filter(Veiculo.tipo == usuario_setor).order_by(Motorista.nome_completo).distinct().all()
    else:
        veiculos = Veiculo.query.order_by(Veiculo.placa).all()
        motoristas = Motorista.query.order_by(Motorista.nome_completo).all()

    # Filtros aplicados para badges
    filtros_aplicados = {}
    if usuario_tipo == "admin" and setor_filtro:
        filtros_aplicados["setor"] = setor_filtro
    if data_inicio:
        try:
            filtros_aplicados["data_inicio"] = datetime.fromisoformat(data_inicio).strftime('%d/%m/%Y')
        except Exception:
            filtros_aplicados["data_inicio"] = data_inicio
    if data_fim:
        try:
            filtros_aplicados["data_fim"] = datetime.fromisoformat(data_fim).strftime('%d/%m/%Y')
        except Exception:
            filtros_aplicados["data_fim"] = data_fim
    if veiculo_id:
        veiculo = Veiculo.query.get(veiculo_id)
        filtros_aplicados["veiculo_id"] = veiculo.placa if veiculo else veiculo_id
    if combustivel:
        filtros_aplicados["combustivel"] = combustivel

    # Renderizar template com todos os dados
    # Listar setores disponíveis para o filtro (admin)
    setores = []
    if usuario_tipo == "admin":
        setores = [s[0] for s in db.session.query(User.setor).filter(User.setor != None).distinct().order_by(User.setor).all() if s[0]]

    return render_template(
        "dashboard.html",
        abastecimentos=abastecimentos,
        veiculos=veiculos,
        motoristas=motoristas,
        tipos_combustivel=TIPOS_COMBUSTIVEL,
        filtros={
            'data_inicio': data_inicio,
            'data_fim': data_fim,
            'veiculo_id': veiculo_id,
            'motorista_id': motorista_id,
            'combustivel': combustivel,
            'agrupamento': agrupamento,
            'setor': setor_filtro
        },
        setores=setores,
        indicadores={
            'total_litros': round(total_litros, 2),
            'valor_total': valor_total,
            'media_litros': round(media_litros, 2),
            'total_veiculos': total_veiculos
        },
        graficos={
            'litros_por_dia': {
                'labels': list(litros_por_periodo_ordenado.keys()),
                'data': list(litros_por_periodo_ordenado.values())
            },
            'litros_por_veiculo': {
                'labels': list(litros_por_veiculo_top10.keys()),
                'data': list(litros_por_veiculo_top10.values())
            },
            'litros_por_motorista': {
                'labels': list(litros_por_motorista_top10.keys()),
                'data': list(litros_por_motorista_top10.values())
            },
            'litros_por_combustivel': {
                'labels': list(litros_por_combustivel.keys()),
                'data': list(litros_por_combustivel.values())
            }
        }
    )



# Nova rota de login com autenticação pelo modelo User
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        senha = request.form["password"]
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.senha_hash, senha):
            session["usuario"] = user.email  # <- chave usada para validar sessão
            session["usuario_id"] = user.id
            session["usuario_nome"] = user.nome
            session["usuario_tipo"] = user.tipo
            session["usuario_setor"] = user.setor
            # Cookie de lembrar email
            response = make_response(redirect(url_for("dashboard")))
            if 'remember' in request.form:
                response.set_cookie('remembered_email', email, max_age=30*24*60*60)
            else:
                response.set_cookie('remembered_email', '', expires=0)
            return response
        else:
            return render_template("login.html", error="Usuário ou senha inválidos")
    # Para requisições GET, verificar se há email lembrado
    email_lembrado = request.cookies.get('remembered_email', '')
    return render_template("login.html", email_lembrado=email_lembrado)


# Atualizar logout para limpar todos os dados de sessão do usuário
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/recover-password", methods=["GET", "POST"])
def recover_password():
    if request.method == "POST":
        email = request.form.get("email")
        return redirect(url_for("login"))
    return render_template("recover_password.html")

# ----------------------
# ROTAS PARA VEÍCULOS
# ----------------------
@app.route("/veiculos", methods=["GET", "POST"])
def veiculos():
    if "usuario" not in session:
        return redirect(url_for("login"))
    if request.method == "POST":
        try:
            placa = request.form["plate"]
            combustivel = request.form["fuel_type"]
            capacidade_tanque = float(request.form["tank_capacity"]) if request.form["tank_capacity"] else None
            # Se admin, pega o setor do campo, senão usa o tipo do formulário
            if session.get("usuario_tipo") == "admin":
                tipo = request.form["setor"]
            else:
                tipo = request.form["type"]
            novo_veiculo = Veiculo(placa=placa, tipo=tipo, combustivel=combustivel, capacidade_tanque=capacidade_tanque)
            db.session.add(novo_veiculo)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
        return redirect(url_for("veiculos"))
    usuario_tipo = session.get("usuario_tipo")
    usuario_setor = session.get("usuario_setor")
    if usuario_tipo != "admin" and usuario_setor:
        veiculos_lista = Veiculo.query.filter(Veiculo.tipo == usuario_setor).order_by(Veiculo.placa).all()
        setores = []
    else:
        setor_filtro = request.args.get('setor')
        if setor_filtro:
            veiculos_lista = Veiculo.query.filter(Veiculo.tipo == setor_filtro).order_by(Veiculo.placa).all()
        else:
            veiculos_lista = Veiculo.query.order_by(Veiculo.placa).all()
        # Listar setores disponíveis para o admin
        setores = [s[0] for s in db.session.query(User.setor).filter(User.setor != None).distinct().order_by(User.setor).all() if s[0]]
    return render_template("veiculos.html", items=veiculos_lista, tipos_combustivel=TIPOS_COMBUSTIVEL, setores=setores)

@app.route("/veiculos/<int:veiculo_id>/editar", methods=["GET", "POST"])
def editar_veiculo(veiculo_id):
    if "usuario" not in session:
        return redirect(url_for("login"))
    veiculo = Veiculo.query.get_or_404(veiculo_id)
    if request.method == "POST":
        try:
            veiculo.placa = request.form["plate"]
            veiculo.combustivel = request.form["fuel_type"]
            veiculo.capacidade_tanque = float(request.form["tank_capacity"]) if request.form["tank_capacity"] else None
            if session.get("usuario_tipo") == "admin":
                veiculo.tipo = request.form["setor"]
            else:
                veiculo.tipo = request.form["type"]
            db.session.commit()
        except Exception as e:
            db.session.rollback()
        return redirect(url_for("veiculos"))
    # Listar setores para admin
    setores = []
    if session.get("usuario_tipo") == "admin":
        setores = [s[0] for s in db.session.query(User.setor).filter(User.setor != None).distinct().order_by(User.setor).all() if s[0]]
    return render_template("editar_veiculo.html", veiculo=veiculo, tipos_combustivel=TIPOS_COMBUSTIVEL, setores=setores)

@app.route("/veiculos/<int:veiculo_id>/excluir", methods=["POST"])
def excluir_veiculo(veiculo_id):
    if "usuario" not in session:
        return redirect(url_for("login"))
    veiculo = Veiculo.query.get_or_404(veiculo_id)
    try:
        db.session.delete(veiculo)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
    return redirect(url_for("veiculos"))

@app.route("/veiculos/visualizar")
def visualizar_veiculos():
    """Página de visualização da lista de veículos antes da impressão"""
    if "usuario" not in session:
        return redirect(url_for("login"))
    
    usuario_tipo = session.get("usuario_tipo")
    usuario_setor = session.get("usuario_setor")
    if usuario_tipo != "admin" and usuario_setor:
        veiculos = Veiculo.query.filter(Veiculo.tipo == usuario_setor).order_by(Veiculo.placa).all()
    else:
        veiculos = Veiculo.query.order_by(Veiculo.placa).all()
    agora = datetime.now()
    
    return render_template(
        "veiculos_print.html",
        veiculos=veiculos,
        agora=agora
    )
    
# ----------------------
# ROTAS PARA MOTORISTAS
# ----------------------
@app.route("/motoristas", methods=["GET", "POST"])
def motoristas():
    if "usuario" not in session:
        return redirect(url_for("login"))
    if request.method == "POST":
        try:
            nome_completo = request.form["full_name"]
            documento = request.form["document"]
            observacoes = request.form["observations"]
            setor = None
            usuario_tipo = session.get("usuario_tipo")
            if usuario_tipo == "admin":
                setor = request.form.get("setor")
            elif session.get("usuario_setor"):
                setor = session.get("usuario_setor")
            novo_motorista = Motorista(nome_completo=nome_completo, documento=documento, observacoes=observacoes, setor=setor)
            db.session.add(novo_motorista)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
        return redirect(url_for("motoristas"))
    usuario_tipo = session.get("usuario_tipo")
    usuario_setor = session.get("usuario_setor")
    setor_filtro = request.args.get('setor')
    if usuario_tipo == "admin":
        query = Motorista.query
        if setor_filtro:
            query = query.filter(Motorista.setor == setor_filtro)
        motoristas_lista = query.order_by(Motorista.nome_completo).all()
        setores = [s[0] for s in db.session.query(User.setor).filter(User.setor != None).distinct().order_by(User.setor).all() if s[0]]
    elif usuario_setor:
        motoristas_lista = Motorista.query.join(Abastecimento).join(Veiculo).filter(Veiculo.tipo == usuario_setor).order_by(Motorista.nome_completo).distinct().all()
        setores = []
    else:
        motoristas_lista = Motorista.query.order_by(Motorista.nome_completo).all()
        setores = []
    return render_template("motoristas.html", items=motoristas_lista, setores=setores)

@app.route("/motoristas/<int:motorista_id>/editar", methods=["GET", "POST"])
def editar_motorista(motorista_id):
    if "usuario" not in session:
        return redirect(url_for("login"))
    motorista = Motorista.query.get_or_404(motorista_id)
    if request.method == "POST":
        try:
            motorista.nome_completo = request.form["full_name"]
            motorista.documento = request.form["document"]
            motorista.observacoes = request.form["observations"]
            usuario_tipo = session.get("usuario_tipo")
            if usuario_tipo == "admin":
                motorista.setor = request.form.get("setor")
            db.session.commit()
        except Exception as e:
            db.session.rollback()
        return redirect(url_for("motoristas"))
    setores = []
    if session.get("usuario_tipo") == "admin":
        setores = [s[0] for s in db.session.query(User.setor).filter(User.setor != None).distinct().order_by(User.setor).all() if s[0]]
    return render_template("editar_motorista.html", motorista=motorista, setores=setores)

@app.route("/motoristas/<int:motorista_id>/excluir", methods=["POST"])
def excluir_motorista(motorista_id):
    if "usuario" not in session:
        return redirect(url_for("login"))
    motorista = Motorista.query.get_or_404(motorista_id)
    try:
        db.session.delete(motorista)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
    return redirect(url_for("motoristas"))

@app.route("/motoristas/visualizar")
def visualizar_motoristas():
    """Página de visualização da lista de motoristas antes da impressão"""
    if "usuario" not in session:
        return redirect(url_for("login"))
    
    usuario_tipo = session.get("usuario_tipo")
    usuario_setor = session.get("usuario_setor")
    if usuario_tipo != "admin" and usuario_setor:
        motoristas = Motorista.query.join(Abastecimento).join(Veiculo).filter(Veiculo.tipo == usuario_setor).order_by(Motorista.nome_completo).distinct().all()
    else:
        motoristas = Motorista.query.order_by(Motorista.nome_completo).all()
    agora = datetime.now()
    
    return render_template(
        "motoristas_print.html",
        motoristas=motoristas,
        agora=agora
    )
    
# ----------------------
# ROTAS PARA ABASTECIMENTOS
# ----------------------
@app.route("/abastecimentos", methods=["GET", "POST"])
def abastecimentos_view():
    if "usuario" not in session:
        return redirect(url_for("login"))
    if request.method == "POST":
        try:
            data = datetime.fromisoformat(request.form["date"])
            veiculo_id = int(request.form["vehicle_id"])
            motorista_id = int(request.form["driver_id"])
            hodometro = int(request.form["odometer"])
            litros = float(request.form["liters"])
            valor_total = float(request.form["total_value"])
            numero_nota = request.form["invoice_number"]
            observacoes = request.form["observations"]
            contrato_id = request.form.get("contrato_id")
            contrato_id = int(contrato_id) if contrato_id else None

            veiculo = Veiculo.query.get(veiculo_id)
            if not veiculo:
                return redirect(url_for("abastecimentos_view"))

            novo_abastecimento = Abastecimento(
                data=data,
                veiculo_id=veiculo_id,
                motorista_id=motorista_id,
                hodometro=hodometro,
                litros=litros,
                valor_total=valor_total,
                numero_nota=numero_nota,
                observacoes=observacoes,
                combustivel=veiculo.combustivel,
                contrato_id=contrato_id
            )
            db.session.add(novo_abastecimento)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
        return redirect(url_for("abastecimentos_view"))

    usuario_tipo = session.get("usuario_tipo")
    usuario_setor = session.get("usuario_setor")
    setores = []
    if usuario_tipo == "admin":
        setores = [s[0] for s in db.session.query(User.setor).filter(User.setor != None).distinct().order_by(User.setor).all() if s[0]]
        setor_selecionado = request.form.get("setor") if request.method == "POST" else None
        if setor_selecionado:
            veiculos = Veiculo.query.filter(Veiculo.tipo == setor_selecionado).order_by(Veiculo.placa).all()
        else:
            veiculos = Veiculo.query.order_by(Veiculo.placa).all()
    elif usuario_setor:
        veiculos = Veiculo.query.filter(Veiculo.tipo == usuario_setor).order_by(Veiculo.placa).all()
    else:
        veiculos = Veiculo.query.order_by(Veiculo.placa).all()
    # Motoristas e abastecimentos permanecem como antes
    if usuario_tipo != "admin" and usuario_setor:
        motoristas = Motorista.query.join(Abastecimento).join(Veiculo).filter(Veiculo.tipo == usuario_setor).order_by(Motorista.nome_completo).distinct().all()
        abastecimentos_lista = Abastecimento.query.join(Veiculo).filter(Veiculo.tipo == usuario_setor).order_by(Abastecimento.data.desc()).all()
    else:
        motoristas = Motorista.query.order_by(Motorista.nome_completo).all()
        abastecimentos_lista = Abastecimento.query.order_by(Abastecimento.data.desc()).all()
    contratos_ativos = ContratoCombustivel.query.filter_by(ativo=True).all()

    return render_template(
        "abastecimento.html",
        items=abastecimentos_lista,
        vehicles=veiculos,
        drivers=motoristas,
        contratos_ativos=contratos_ativos,
        setores=setores
    )

@app.route("/abastecimentos/<int:abastecimento_id>/editar", methods=["GET", "POST"])
def editar_abastecimento(abastecimento_id):
    if "usuario" not in session:
        return redirect(url_for("login"))
    abastecimento = Abastecimento.query.get_or_404(abastecimento_id)
    usuario_tipo = session.get("usuario_tipo")
    usuario_setor = session.get("usuario_setor")
    setores = []
    if usuario_tipo == "admin":
        setores = [s[0] for s in db.session.query(User.setor).filter(User.setor != None).distinct().order_by(User.setor).all() if s[0]]
        setor_selecionado = request.form.get("setor") if request.method == "POST" else (abastecimento.veiculo.tipo if abastecimento.veiculo else None)
        if setor_selecionado:
            veiculos = Veiculo.query.filter(Veiculo.tipo == setor_selecionado).order_by(Veiculo.placa).all()
        else:
            veiculos = Veiculo.query.order_by(Veiculo.placa).all()
    elif usuario_setor:
        veiculos = Veiculo.query.filter(Veiculo.tipo == usuario_setor).order_by(Veiculo.placa).all()
    else:
        veiculos = Veiculo.query.order_by(Veiculo.placa).all()
    motoristas = Motorista.query.order_by(Motorista.nome_completo).all()
    if request.method == "POST":
        try:
            abastecimento.data = datetime.fromisoformat(request.form["date"])
            abastecimento.veiculo_id = int(request.form["vehicle_id"])
            abastecimento.motorista_id = int(request.form["driver_id"])
            abastecimento.hodometro = int(request.form["odometer"])
            abastecimento.litros = float(request.form["liters"])
            abastecimento.valor_total = float(request.form["total_value"])
            abastecimento.numero_nota = request.form["invoice_number"]
            abastecimento.observacoes = request.form["observations"]
            db.session.commit()
        except Exception as e:
            db.session.rollback()
        return redirect(url_for("abastecimentos_view"))
    return render_template("editar_abastecimento.html", abastecimento=abastecimento, vehicles=veiculos, drivers=motoristas, setores=setores)

@app.route("/abastecimentos/<int:abastecimento_id>/excluir", methods=["POST"])
def excluir_abastecimento(abastecimento_id):
    if "usuario" not in session:
        return redirect(url_for("login"))
    abastecimento = Abastecimento.query.get_or_404(abastecimento_id)
    try:
        db.session.delete(abastecimento)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
    return redirect(url_for("abastecimentos_view"))

# ----------------------
# ROTAS PARA CONTRATOS DE COMBUSTÍVEL
# ----------------------
@app.route("/contratos-combustivel", methods=["GET", "POST"])
def contratos_combustivel():
    if "usuario" not in session:
        return redirect(url_for("login"))

    if request.method == "POST":
        try:
            numero_contrato = request.form.get("numero_contrato", "").strip()
            ano_contrato = int(request.form.get("ano_contrato"))
            data_inicio_contrato = datetime.strptime(request.form["data_inicio_contrato"], '%Y-%m-%d').date()
            data_fim_contrato = datetime.strptime(request.form["data_fim_contrato"], '%Y-%m-%d').date()
            fornecedor = request.form.get("fornecedor", "").strip()
            observacoes = request.form.get("observacoes", "").strip()
            setor = request.form.get("setor") if session.get("usuario_tipo") == "admin" else None

            tipos = request.form.getlist("combustiveis_tipo[]")
            quantidades = request.form.getlist("combustiveis_quantidade[]")
            valores_totais = request.form.getlist("combustiveis_valor_total[]")

            novo_contrato = ContratoCombustivel(
                numero_contrato=numero_contrato,
                ano_contrato=ano_contrato,
                data_inicio_contrato=data_inicio_contrato,
                data_fim_contrato=data_fim_contrato,
                fornecedor=fornecedor,
                observacoes=observacoes,
                setor=setor,
                ativo=True
            )
            db.session.add(novo_contrato)
            db.session.flush()

            for i in range(len(tipos)):
                tipo = tipos[i]
                quantidade = float(quantidades[i]) if quantidades[i] else 0
                valor_total = float(valores_totais[i]) if valores_totais[i] else 0
                valor_por_litro = valor_total / quantidade if quantidade > 0 else 0
                item = ContratoCombustivelItem(
                    contrato_id=novo_contrato.id,
                    tipo_combustivel=tipo,
                    quantidade=quantidade,
                    valor_total=valor_total,
                    valor_por_litro=valor_por_litro
                )
                db.session.add(item)

            db.session.commit()
            flash("Contrato de combustível cadastrado com sucesso!", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Erro ao cadastrar contrato: {e}", "error")
        
        return redirect(url_for("contratos_combustivel"))


    # ==============================
    # CÁLCULO DOS DADOS DO RELATÓRIO
    # ==============================
    usuario_tipo = session.get("usuario_tipo")
    usuario_setor = session.get("usuario_setor")
    setor_filtro = request.args.get('setor')
    query = ContratoCombustivel.query.filter_by(ativo=True)
    if usuario_tipo != "admin" and usuario_setor:
        query = query.filter(ContratoCombustivel.setor == usuario_setor)
    elif usuario_tipo == "admin" and setor_filtro:
        query = query.filter(ContratoCombustivel.setor == setor_filtro)
    contratos = query.order_by(desc(ContratoCombustivel.data_criacao)).all()
    dados_relatorio = []
    total_contratos_ativos = len(contratos)
    total_valor_contratado = 0
    total_valor_consumido = 0
    total_valor_restante = 0
    
    for contrato in contratos:
        for item in contrato.itens:
            quantidade_contratada = item.quantidade
            valor_total_contratado = item.valor_total
            
            inicio_contrato = datetime.combine(contrato.data_inicio_contrato, datetime.min.time())
            fim_contrato = datetime.combine(contrato.data_fim_contrato, datetime.max.time())
            
            # Filtro de setor para abastecimentos
            if usuario_tipo != "admin" and usuario_setor:
                abastecimentos_tipo = Abastecimento.query.join(Veiculo).filter(
                    Veiculo.combustivel == item.tipo_combustivel,
                    Veiculo.tipo == usuario_setor,
                    Abastecimento.data >= inicio_contrato,
                    Abastecimento.data <= fim_contrato
                ).all()
                abastecimentos_contrato = Abastecimento.query.join(Veiculo).filter(
                    Abastecimento.contrato_id == contrato.id,
                    Veiculo.tipo == usuario_setor,
                    Abastecimento.data >= inicio_contrato,
                    Abastecimento.data <= fim_contrato
                ).all()
            else:
                abastecimentos_tipo = Abastecimento.query.join(Veiculo).filter(
                    Veiculo.combustivel == item.tipo_combustivel,
                    Abastecimento.data >= inicio_contrato,
                    Abastecimento.data <= fim_contrato
                ).all()
                abastecimentos_contrato = Abastecimento.query.filter(
                    Abastecimento.contrato_id == contrato.id,
                    Abastecimento.data >= inicio_contrato,
                    Abastecimento.data <= fim_contrato
                ).all()
            todos_abastecimentos = list(set(abastecimentos_tipo + abastecimentos_contrato))
            quantidade_consumida = sum(a.litros for a in todos_abastecimentos)
            valor_usado = sum(a.valor_total for a in todos_abastecimentos)
            
            quantidade_restante = max(0, quantidade_contratada - quantidade_consumida)
            valor_restante = max(0, valor_total_contratado - valor_usado)
            
            percentual_consumido = (quantidade_consumida / quantidade_contratada * 100) if quantidade_contratada > 0 else 0
            
            dados_relatorio.append({
                'tipo_combustivel': item.tipo_combustivel,
                'fornecedor': contrato.fornecedor,
                'numero_contrato': contrato.numero_contrato,
                'ano_contrato': contrato.ano_contrato,
                'data_inicio_contrato': contrato.data_inicio_contrato,
                'data_fim_contrato': contrato.data_fim_contrato,
                'quantidade_contratada': quantidade_contratada,
                'valor_total': valor_total_contratado,
                'valor_por_litro': item.valor_por_litro,
                'quantidade_consumida': quantidade_consumida,
                'valor_usado': valor_usado,
                'quantidade_restante': quantidade_restante,
                'valor_restante': valor_restante,
                'percentual_consumido': percentual_consumido
            })
            
            total_valor_contratado += valor_total_contratado
            total_valor_consumido += valor_usado
            total_valor_restante += valor_restante
    
    hoje = date.today()
    agora = datetime.now()  # Definido aqui para uso no template
    
    setores = []
    if session.get("usuario_tipo") == "admin":
        setores = [s[0] for s in db.session.query(User.setor).filter(User.setor != None).distinct().order_by(User.setor).all() if s[0]]
    return render_template(
        "contratos_combustivel.html",
        dados=dados_relatorio,
        total_contratos=total_contratos_ativos,
        total_valor_contratado=total_valor_contratado,
        total_valor_consumido=total_valor_consumido,
        total_valor_restante=total_valor_restante,
        items=contratos,
        tipos_combustivel=TIPOS_COMBUSTIVEL,
        hoje=hoje,
        agora=agora,
        setores=setores
    )

@app.route("/contratos-combustivel/<int:contrato_id>/editar", methods=["GET", "POST"])
def editar_contrato_combustivel(contrato_id):
    if "usuario" not in session:
        return redirect(url_for("login"))
    contrato = ContratoCombustivel.query.get_or_404(contrato_id)
    setores = []
    if session.get("usuario_tipo") == "admin":
        setores = [s[0] for s in db.session.query(User.setor).filter(User.setor != None).distinct().order_by(User.setor).all() if s[0]]
    if request.method == "POST":
        try:
            contrato.numero_contrato = request.form.get("numero_contrato", "").strip()
            contrato.ano_contrato = int(request.form.get("ano_contrato"))
            contrato.data_inicio_contrato = datetime.strptime(request.form["data_inicio_contrato"], '%Y-%m-%d').date()
            contrato.data_fim_contrato = datetime.strptime(request.form["data_fim_contrato"], '%Y-%m-%d').date()
            contrato.fornecedor = request.form.get("fornecedor", "").strip()
            contrato.observacoes = request.form.get("observacoes", "").strip()
            if session.get("usuario_tipo") == "admin":
                contrato.setor = request.form.get("setor")

            tipos = request.form.getlist("combustiveis_tipo[]")
            quantidades = request.form.getlist("combustiveis_quantidade[]")
            valores_totais = request.form.getlist("combustiveis_valor_total[]")

            contrato.itens.clear()
            for i in range(len(tipos)):
                tipo = tipos[i]
                quantidade = float(quantidades[i]) if quantidades[i] else 0
                valor_total = float(valores_totais[i]) if valores_totais[i] else 0
                valor_por_litro = valor_total / quantidade if quantidade > 0 else 0
                item = ContratoCombustivelItem(
                    tipo_combustivel=tipo,
                    quantidade=quantidade,
                    valor_total=valor_total,
                    valor_por_litro=valor_por_litro
                )
                contrato.itens.append(item)

            db.session.commit()
        except Exception as e:
            db.session.rollback()
        return redirect(url_for("contratos_combustivel"))
    return render_template(
        "editar_contrato_combustivel.html",
        contrato=contrato,
        combustiveis_itens=contrato.itens,
        tipos_combustivel=TIPOS_COMBUSTIVEL,
        setores=setores
    )

@app.route("/contratos-combustivel/<int:contrato_id>/excluir", methods=["POST"])
def excluir_contrato_combustivel(contrato_id):
    if "usuario" not in session:
        return redirect(url_for("login"))
    contrato = ContratoCombustivel.query.get_or_404(contrato_id)
    try:
        contrato.ativo = False
        db.session.commit()
    except Exception as e:
        db.session.rollback()
    return redirect(url_for("contratos_combustivel"))

# ----------------------
# ROTAS PARA ADITIVOS DE CONTRATO DE COMBUSTÍVEL
# ----------------------

@app.route("/contratos-combustivel/<int:contrato_id>/aditivos")
def listar_aditivos(contrato_id):
    if "usuario" not in session:
        return redirect(url_for("login"))
    contrato = ContratoCombustivel.query.get_or_404(contrato_id)
    aditivos = AditivoContratoCombustivel.query.filter_by(contrato_id=contrato_id).order_by(AditivoContratoCombustivel.data_aditivo.desc()).all()
    return render_template("aditivos_contrato.html", contrato=contrato, aditivos=aditivos)

@app.route("/contratos-combustivel/<int:contrato_id>/aditivos/novo", methods=["GET", "POST"])
def novo_aditivo(contrato_id):
    if "usuario" not in session:
        return redirect(url_for("login"))
    contrato = ContratoCombustivel.query.get_or_404(contrato_id)

    if request.method == "POST":
        # Build tipo_aditivo based on checkboxes
        modificacoes = []
        if request.form.get("modificar_prazo"):
            modificacoes.append("Prorrogação")
        if request.form.get("modificar_valor"):
            modificacoes.append("Reajuste de Valor")
        if request.form.get("modificar_quantidade"):
            modificacoes.append("Aumento de Quantidade")
        tipo_aditivo = ", ".join(modificacoes) if modificacoes else None

        if not tipo_aditivo:
            flash("Selecione pelo menos uma modificação.", "error")
            return redirect(url_for("novo_aditivo", contrato_id=contrato_id))

        descricao = request.form.get("descricao")
        data_aditivo = request.form.get("data_aditivo")
        novo_valor_total = request.form.get("novo_valor_total")
        nova_quantidade_total = request.form.get("nova_quantidade_total")
        nova_data_fim = request.form.get("nova_data_fim")

        aditivo = AditivoContratoCombustivel(
            contrato_id=contrato_id,
            tipo_aditivo=tipo_aditivo,
            descricao=descricao,
            data_aditivo=datetime.strptime(data_aditivo, "%Y-%m-%d") if data_aditivo else None,
            novo_valor_total=float(novo_valor_total) if novo_valor_total else None,
            nova_quantidade_total=float(nova_quantidade_total) if nova_quantidade_total else None,
            nova_data_fim=datetime.strptime(nova_data_fim, "%Y-%m-%d") if nova_data_fim else None
        )
        db.session.add(aditivo)
        db.session.commit()
        flash("Aditivo cadastrado com sucesso!", "success")
        return redirect(url_for("listar_aditivos", contrato_id=contrato_id))

    agora = datetime.now()

    return render_template("editar_aditivo.html", contrato=contrato, aditivo=None, agora=agora)

@app.route("/contratos-combustivel/<int:contrato_id>/aditivos/<int:aditivo_id>/editar", methods=["GET", "POST"])
def editar_aditivo(contrato_id, aditivo_id):
    if "usuario" not in session:
        return redirect(url_for("login"))
    contrato = ContratoCombustivel.query.get_or_404(contrato_id)
    aditivo = AditivoContratoCombustivel.query.get_or_404(aditivo_id)

    if request.method == "POST":
        # Build tipo_aditivo based on checkboxes
        modificacoes = []
        if request.form.get("modificar_prazo"):
            modificacoes.append("Prorrogação")
        if request.form.get("modificar_valor"):
            modificacoes.append("Reajuste de Valor")
        if request.form.get("modificar_quantidade"):
            modificacoes.append("Aumento de Quantidade")
        tipo_aditivo = ", ".join(modificacoes) if modificacoes else None

        if not tipo_aditivo:
            flash("Selecione pelo menos uma modificação.", "danger")
            return redirect(url_for("editar_aditivo", contrato_id=contrato_id, aditivo_id=aditivo_id))

        aditivo.tipo_aditivo = tipo_aditivo
        aditivo.descricao = request.form.get("descricao")
        data_aditivo = request.form.get("data_aditivo")
        aditivo.data_aditivo = datetime.strptime(data_aditivo, "%Y-%m-%d") if data_aditivo else None
        novo_valor_total = request.form.get("novo_valor_total")
        aditivo.novo_valor_total = float(novo_valor_total) if novo_valor_total else None
        nova_quantidade_total = request.form.get("nova_quantidade_total")
        aditivo.nova_quantidade_total = float(nova_quantidade_total) if nova_quantidade_total else None
        nova_data_fim = request.form.get("nova_data_fim")
        aditivo.nova_data_fim = datetime.strptime(nova_data_fim, "%Y-%m-%d") if nova_data_fim else None
        dias_adicionais = request.form.get("dias_adicionais")
        aditivo.dias_adicionais = int(dias_adicionais) if dias_adicionais else None

        db.session.commit()
        flash("Aditivo atualizado com sucesso!", "success")
        return redirect(url_for("listar_aditivos", contrato_id=contrato_id))

    agora = datetime.now()

    return render_template("editar_aditivo.html", contrato=contrato, aditivo=aditivo, agora=agora)

@app.route("/contratos-combustivel/<int:contrato_id>/aditivos/<int:aditivo_id>/excluir", methods=["POST"])
def excluir_aditivo(contrato_id, aditivo_id):
    if "usuario" not in session:
        return redirect(url_for("login"))
    aditivo = AditivoContratoCombustivel.query.get_or_404(aditivo_id)
    db.session.delete(aditivo)
    db.session.commit()
    flash("Aditivo excluído com sucesso!", "success")
    return redirect(url_for("listar_aditivos", contrato_id=contrato_id))

@app.route("/usuarios")
def usuarios():
    if "usuario" not in session:
        return redirect(url_for("login"))
    # Listar todos os usuários
    usuarios = User.query.order_by(User.nome).all()
    return render_template("usuarios.html", usuarios=usuarios)

@app.route("/usuarios/novo", methods=["GET", "POST"])
def criar_usuario():
    if "usuario" not in session:
        return redirect(url_for("login"))
    if request.method == "POST":
        try:
            nome = request.form.get("nome", "").strip()
            email = request.form.get("email", "").strip().lower()
            senha = request.form.get("senha", "").strip()
            tipo = request.form.get("tipo", "").strip()
            setor = request.form.get("setor", "").strip() if tipo == "departamento" else None

            # Verificar se o email já existe
            if User.query.filter_by(email=email).first():
                flash("E-mail já cadastrado.", "error")
                return redirect(url_for("criar_usuario"))

            # Criar novo usuário
            novo_usuario = User(
                nome=nome,
                email=email,
                tipo=tipo,
                setor=setor
            )
            novo_usuario.set_password(senha)
            db.session.add(novo_usuario)
            db.session.commit()
            flash("Usuário criado com sucesso!", "success")
            return redirect(url_for("usuarios"))
        except Exception as e:
            db.session.rollback()
            flash(f"Erro ao criar usuário: {e}", "error")
            return redirect(url_for("criar_usuario"))
    return render_template("criar_usuario.html")

@app.route("/usuarios/<int:user_id>/editar", methods=["GET", "POST"])
def editar_usuario(user_id):
    if "usuario" not in session:
        return redirect(url_for("login"))
    usuario = User.query.get_or_404(user_id)
    if request.method == "POST":
        try:
            nome = request.form.get("nome", "").strip()
            email = request.form.get("email", "").strip().lower()
            senha = request.form.get("senha", "").strip()
            tipo = request.form.get("tipo", "").strip()
            setor = request.form.get("setor", "").strip() if tipo == "departamento" else None

            # Verificar se o email já existe em outro usuário
            existing_user = User.query.filter_by(email=email).first()
            if existing_user and existing_user.id != user_id:
                flash("E-mail já cadastrado.", "error")
                return redirect(url_for("editar_usuario", user_id=user_id))

            usuario.nome = nome
            usuario.email = email
            usuario.tipo = tipo
            usuario.setor = setor
            if senha:
                usuario.set_password(senha)
            db.session.commit()
            flash("Usuário atualizado com sucesso!", "success")
            return redirect(url_for("usuarios"))
        except Exception as e:
            db.session.rollback()
            flash(f"Erro ao atualizar usuário: {e}", "error")
            return redirect(url_for("editar_usuario", user_id=user_id))
    return render_template("editar_usuario.html", usuario=usuario)

@app.route("/usuarios/<int:user_id>/excluir", methods=["POST"])
def excluir_usuario(user_id):
    if "usuario" not in session:
        return redirect(url_for("login"))
    usuario = User.query.get_or_404(user_id)
    try:
        db.session.delete(usuario)
        db.session.commit()
        flash("Usuário excluído com sucesso!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Erro ao excluir usuário: {e}", "error")
    return redirect(url_for("usuarios"))

@app.route("/relatorios/veiculos", endpoint="relatorio_veiculos")
def relatorio_veiculos():
    if "usuario" not in session:
        return redirect(url_for("login"))
    usuario_tipo = session.get("usuario_tipo")
    usuario_setor = session.get("usuario_setor")
    data_inicio = request.args.get("data_inicio", "")
    data_fim = request.args.get("data_fim", "")
    veiculo_id = request.args.get("veiculo_id", "")
    combustivel = request.args.get("combustivel", "")
    setor_filtro = request.args.get("setor", "")
    query = Abastecimento.query.join(Abastecimento.veiculo).join(Abastecimento.motorista)
    if usuario_tipo == "admin" and setor_filtro:
        query = query.filter(Veiculo.tipo == setor_filtro)
    elif usuario_tipo != "admin" and usuario_setor:
        query = query.filter(Veiculo.tipo == usuario_setor)
    # Filtros de data
    if data_inicio:
        try:
            data_inicio_obj = datetime.fromisoformat(data_inicio)
            query = query.filter(Abastecimento.data >= data_inicio_obj)
        except (ValueError, TypeError):
            data_inicio = ""
    if data_fim:
        try:
            data_fim_obj = datetime.fromisoformat(data_fim)
            data_fim_obj = data_fim_obj.replace(hour=23, minute=59, second=59)
            query = query.filter(Abastecimento.data <= data_fim_obj)
        except (ValueError, TypeError):
            data_fim = ""
    # Filtros adicionais
    if veiculo_id:
        try:
            query = query.filter(Abastecimento.veiculo_id == int(veiculo_id))
        except (ValueError, TypeError):
            veiculo_id = ""
    if combustivel:
        query = query.filter(Veiculo.combustivel == combustivel)
    abastecimentos = query.order_by(desc(Abastecimento.data)).all()
    # Calcular dados para o relatório
    dados_veiculos = {}
    for abastecimento in abastecimentos:
        veiculo = abastecimento.veiculo
        motorista = abastecimento.motorista
        if veiculo not in dados_veiculos:
            dados_veiculos[veiculo] = {
                'total_litros': 0,
                'total_valor': 0,
                'total_abastecimentos': 0,
                'media_litros': 0,
                'motoristas_mais_utilizados': {},
                'abastecimentos': []
            }
        dados_veiculos[veiculo]['total_litros'] += abastecimento.litros
        dados_veiculos[veiculo]['total_valor'] += abastecimento.valor_total
        dados_veiculos[veiculo]['total_abastecimentos'] += 1
        dados_veiculos[veiculo]['abastecimentos'].append(abastecimento)
        if motorista not in dados_veiculos[veiculo]['motoristas_mais_utilizados']:
            dados_veiculos[veiculo]['motoristas_mais_utilizados'][motorista] = {
                'litros': 0,
                'valor': 0
            }
        dados_veiculos[veiculo]['motoristas_mais_utilizados'][motorista]['litros'] += abastecimento.litros
        dados_veiculos[veiculo]['motoristas_mais_utilizados'][motorista]['valor'] += abastecimento.valor_total
    # Calcular média e preparar dados para gráfico
    for veiculo, dados in dados_veiculos.items():
        if dados['total_abastecimentos'] > 0:
            dados['media_litros'] = dados['total_litros'] / dados['total_abastecimentos']
        else:
            dados['media_litros'] = 0
        motoristas_ordenados = sorted(
            dados['motoristas_mais_utilizados'].items(),
            key=lambda x: x[1]['litros'],
            reverse=True
        )[:5]
        dados['motoristas_mais_utilizados'] = dict(motoristas_ordenados)
        dados['abastecimentos'] = dados['abastecimentos'][:5]
    dados_veiculos = {k: v for k, v in dados_veiculos.items() if v['total_abastecimentos'] > 0}
    filtros_aplicados = {}
    if data_inicio:
        filtros_aplicados['data_inicio'] = datetime.fromisoformat(data_inicio).strftime('%d/%m/%Y')
    if data_fim:
        filtros_aplicados['data_fim'] = datetime.fromisoformat(data_fim).strftime('%d/%m/%Y')
    if veiculo_id:
        veiculo = Veiculo.query.get(veiculo_id)
        filtros_aplicados['veiculo_id'] = veiculo.placa if veiculo else veiculo_id
    if combustivel:
        filtros_aplicados['combustivel'] = combustivel
    if setor_filtro:
        filtros_aplicados['setor'] = setor_filtro
    setores = []
    if session.get("usuario_tipo") == "admin":
        setores = [s[0] for s in db.session.query(User.setor).filter(User.setor != None).distinct().order_by(User.setor).all() if s[0]]
    if usuario_tipo != "admin" and usuario_setor:
        veiculos = Veiculo.query.filter(Veiculo.tipo == usuario_setor).order_by(Veiculo.placa).all()
    else:
        veiculos = Veiculo.query.order_by(Veiculo.placa).all()
    agora = datetime.now()
    return render_template(
        "relatorio_veiculos.html",
        dados_veiculos=dados_veiculos,
        veiculos=veiculos,
        tipos_combustivel=TIPOS_COMBUSTIVEL,
        filtros_aplicados=filtros_aplicados,
        setores=setores,
        agora=agora,
        total_registros=len(dados_veiculos)
    )

@app.route("/relatorios/veiculos/csv", endpoint="export_csv_relatorio_veiculos")
def export_csv_relatorio_veiculos():
    """Export CSV do relatório de veículos - Versão profissional"""
    if "usuario" not in session:
        return redirect(url_for("login"))
    
    usuario_tipo = session.get("usuario_tipo")
    usuario_setor = session.get("usuario_setor")
    data_inicio = request.args.get("data_inicio", "")
    data_fim = request.args.get("data_fim", "")
    veiculo_id = request.args.get("veiculo_id", "")
    combustivel = request.args.get("combustivel", "")
    setor_filtro = request.args.get("setor", "")
    
    # Construir query base
    query = Abastecimento.query.join(Abastecimento.veiculo).join(Abastecimento.motorista)
    
    # Filtros de setor
    if usuario_tipo == "admin" and setor_filtro:
        query = query.filter(Veiculo.tipo == setor_filtro)
    elif usuario_tipo != "admin" and usuario_setor:
        query = query.filter(Veiculo.tipo == usuario_setor)
    
    # Filtros de data
    if data_inicio:
        try:
            data_inicio_obj = datetime.fromisoformat(data_inicio)
            query = query.filter(Abastecimento.data >= data_inicio_obj)
        except (ValueError, TypeError):
            data_inicio = ""
    if data_fim:
        try:
            data_fim_obj = datetime.fromisoformat(data_fim)
            data_fim_obj = data_fim_obj.replace(hour=23, minute=59, second=59)
            query = query.filter(Abastecimento.data <= data_fim_obj)
        except (ValueError, TypeError):
            data_fim = ""
    
    # Filtros adicionais
    if veiculo_id:
        try:
            query = query.filter(Abastecimento.veiculo_id == int(veiculo_id))
        except (ValueError, TypeError):
            veiculo_id = ""
    if combustivel:
        query = query.filter(Veiculo.combustivel == combustivel)
    
    abastecimentos = query.order_by(desc(Abastecimento.data)).all()
    
    # Calcular dados para o relatório
    dados_veiculos = {}
    for abastecimento in abastecimentos:
        veiculo = abastecimento.veiculo
        motorista = abastecimento.motorista
        
        if veiculo not in dados_veiculos:
            dados_veiculos[veiculo] = {
                'total_litros': 0,
                'total_valor': 0,
                'total_abastecimentos': 0,
                'media_litros': 0,
                'motoristas_mais_utilizados': {},
                'abastecimentos': []
            }
        
        dados_veiculos[veiculo]['total_litros'] += abastecimento.litros
        dados_veiculos[veiculo]['total_valor'] += abastecimento.valor_total
        dados_veiculos[veiculo]['total_abastecimentos'] += 1
        dados_veiculos[veiculo]['abastecimentos'].append(abastecimento)
        
        if motorista not in dados_veiculos[veiculo]['motoristas_mais_utilizados']:
            dados_veiculos[veiculo]['motoristas_mais_utilizados'][motorista] = {
                'litros': 0,
                'valor': 0
            }
        
        dados_veiculos[veiculo]['motoristas_mais_utilizados'][motorista]['litros'] += abastecimento.litros
        dados_veiculos[veiculo]['motoristas_mais_utilizados'][motorista]['valor'] += abastecimento.valor_total
    
    # Calcular médias e preparar dados
    for veiculo, dados in dados_veiculos.items():
        if dados['total_abastecimentos'] > 0:
            dados['media_litros'] = dados['total_litros'] / dados['total_abastecimentos']
        else:
            dados['media_litros'] = 0
        
        motoristas_ordenados = sorted(
            dados['motoristas_mais_utilizados'].items(),
            key=lambda x: x[1]['litros'],
            reverse=True
        )[:5]
        dados['motoristas_mais_utilizados'] = dict(motoristas_ordenados)
        dados['abastecimentos'] = dados['abastecimentos'][:5]
    
    # Filtrar apenas veículos com abastecimentos
    dados_veiculos = {k: v for k, v in dados_veiculos.items() if v['total_abastecimentos'] > 0}
    
    # Criar CSV profissional
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Cabeçalho profissional com informações do relatório
    writer.writerow(["RELATÓRIO DE VEÍCULOS - SISTEMA DE GESTÃO DE COMBUSTÍVEL"])
    writer.writerow([])
    
    # Informações de filtros e data
    writer.writerow(["Data de geração:", datetime.now().strftime('%d/%m/%Y %H:%M')])
    writer.writerow(["Usuário:", session.get("usuario_nome", "N/A")])
    
    if data_inicio or data_fim:
        periodo = f"{data_inicio} a {data_fim}" if data_inicio and data_fim else data_inicio or data_fim
        writer.writerow(["Período:", periodo])
    
    if setor_filtro:
        writer.writerow(["Setor:", setor_filtro])
    
    if combustivel:
        writer.writerow(["Combustível:", combustivel])
    
    writer.writerow(["Total de veículos:", len(dados_veiculos)])
    writer.writerow([])
    writer.writerow([])
    
    # Cabeçalho da tabela principal
    writer.writerow([
        "VEÍCULO",
        "PLACA", 
        "TIPO",
        "COMBUSTÍVEL",
        "TOTAL LITROS",
        "VALOR TOTAL (R$)",
        "MÉDIA LITROS/ABAST.",
        "TOTAL ABASTECIMENTOS"
    ])
    
    # Dados formatados
    for veiculo, dados in sorted(dados_veiculos.items(), key=lambda x: x[0].placa):
        writer.writerow([
            veiculo.placa,
            veiculo.placa,  # Duplicado para manter estrutura, pode remover se quiser
            veiculo.tipo or "N/A",
            veiculo.combustivel or "N/A",
            f"{dados['total_litros']:.2f}",
            f"{dados['total_valor']:.2f}",
            f"{dados['media_litros']:.2f}",
            dados['total_abastecimentos']
        ])
    
    writer.writerow([])
    
    # Totais gerais
    total_geral_litros = sum(dados['total_litros'] for dados in dados_veiculos.values())
    total_geral_valor = sum(dados['total_valor'] for dados in dados_veiculos.values())
    total_geral_abastecimentos = sum(dados['total_abastecimentos'] for dados in dados_veiculos.values())
    
    writer.writerow(["TOTAIS GERAIS:"])
    writer.writerow([
        "",
        "",
        "",
        "",
        f"{total_geral_litros:.2f} L",
        f"R$ {total_geral_valor:.2f}",
        "",
        total_geral_abastecimentos
    ])
    
    writer.writerow([])
    writer.writerow([])
    
    # Detalhamento por motorista (top 3 por veículo)
    writer.writerow(["DETALHAMENTO POR MOTORISTA (TOP 3 POR VEÍCULO)"])
    writer.writerow([])
    writer.writerow(["VEÍCULO", "MOTORISTA", "TOTAL LITROS", "VALOR TOTAL (R$)", "% DO TOTAL"])
    
    for veiculo, dados in sorted(dados_veiculos.items(), key=lambda x: x[0].placa):
        veiculo_total_litros = dados['total_litros']
        
        for i, (motorista, info) in enumerate(list(dados['motoristas_mais_utilizados'].items())[:3]):
            percentual = (info['litros'] / veiculo_total_litros * 100) if veiculo_total_litros > 0 else 0
            
            writer.writerow([
                veiculo.placa if i == 0 else "",  # Evitar repetição da placa
                motorista.nome_completo if motorista else "N/A",
                f"{info['litros']:.2f} L",
                f"R$ {info['valor']:.2f}",
                f"{percentual:.1f}%"
            ])
        writer.writerow([])  # Linha em branco entre veículos
    
    output.seek(0)
    
    # Nome do arquivo com data
    data_arquivo = datetime.now().strftime('%Y%m%d_%H%M')
    filename = f"relatorio_veiculos_{data_arquivo}.csv"
    
    return Response(
        output.getvalue(),
        mimetype='text/csv; charset=utf-8',
        headers={
            "Content-Disposition": f"attachment;filename={filename}",
            "Content-Type": "text/csv; charset=utf-8"
        }
    )

@app.route("/relatorios/veiculos/pdf", endpoint="export_pdf_relatorio_veiculos")
def export_pdf_relatorio_veiculos():
    """Export PDF do relatório de veículos"""
    if "usuario" not in session:
        return redirect(url_for("login"))
    usuario_tipo = session.get("usuario_tipo")
    usuario_setor = session.get("usuario_setor")
    data_inicio = request.args.get("data_inicio", "")
    data_fim = request.args.get("data_fim", "")
    veiculo_id = request.args.get("veiculo_id", "")
    combustivel = request.args.get("combustivel", "")
    setor_filtro = request.args.get("setor", "")
    query = Abastecimento.query.join(Abastecimento.veiculo).join(Abastecimento.motorista)
    if usuario_tipo == "admin" and setor_filtro:
        query = query.filter(Veiculo.tipo == setor_filtro)
    elif usuario_tipo != "admin" and usuario_setor:
        query = query.filter(Veiculo.tipo == usuario_setor)
    # Filtros de data
    if data_inicio:
        try:
            data_inicio_obj = datetime.fromisoformat(data_inicio)
            query = query.filter(Abastecimento.data >= data_inicio_obj)
        except (ValueError, TypeError):
            data_inicio = ""
    if data_fim:
        try:
            data_fim_obj = datetime.fromisoformat(data_fim)
            data_fim_obj = data_fim_obj.replace(hour=23, minute=59, second=59)
            query = query.filter(Abastecimento.data <= data_fim_obj)
        except (ValueError, TypeError):
            data_fim = ""
    # Filtros adicionais
    if veiculo_id:
        try:
            query = query.filter(Abastecimento.veiculo_id == int(veiculo_id))
        except (ValueError, TypeError):
            veiculo_id = ""
    if combustivel:
        query = query.filter(Veiculo.combustivel == combustivel)
    abastecimentos = query.order_by(desc(Abastecimento.data)).all()
    # Calcular dados para o relatório
    dados_veiculos = {}
    for abastecimento in abastecimentos:
        veiculo = abastecimento.veiculo
        motorista = abastecimento.motorista
        if veiculo not in dados_veiculos:
            dados_veiculos[veiculo] = {
                'total_litros': 0,
                'total_valor': 0,
                'total_abastecimentos': 0,
                'media_litros': 0,
                'motoristas_mais_utilizados': {},
                'abastecimentos': []
            }
        dados_veiculos[veiculo]['total_litros'] += abastecimento.litros
        dados_veiculos[veiculo]['total_valor'] += abastecimento.valor_total
        dados_veiculos[veiculo]['total_abastecimentos'] += 1
        dados_veiculos[veiculo]['abastecimentos'].append(abastecimento)
        if motorista not in dados_veiculos[veiculo]['motoristas_mais_utilizados']:
            dados_veiculos[veiculo]['motoristas_mais_utilizados'][motorista] = {
                'litros': 0,
                'valor': 0
            }
        dados_veiculos[veiculo]['motoristas_mais_utilizados'][motorista]['litros'] += abastecimento.litros
        dados_veiculos[veiculo]['motoristas_mais_utilizados'][motorista]['valor'] += abastecimento.valor_total
    # Calcular média e preparar dados para gráfico
    for veiculo, dados in dados_veiculos.items():
        if dados['total_abastecimentos'] > 0:
            dados['media_litros'] = dados['total_litros'] / dados['total_abastecimentos']
        else:
            dados['media_litros'] = 0
        motoristas_ordenados = sorted(
            dados['motoristas_mais_utilizados'].items(),
            key=lambda x: x[1]['litros'],
            reverse=True
        )[:5]
        dados['motoristas_mais_utilizados'] = dict(motoristas_ordenados)
        dados['abastecimentos'] = dados['abastecimentos'][:5]
    dados_veiculos = {k: v for k, v in dados_veiculos.items() if v['total_abastecimentos'] > 0}
    filtros_aplicados = {}
    if data_inicio:
        filtros_aplicados['data_inicio'] = datetime.fromisoformat(data_inicio).strftime('%d/%m/%Y')
    if data_fim:
        filtros_aplicados['data_fim'] = datetime.fromisoformat(data_fim).strftime('%d/%m/%Y')
    if veiculo_id:
        veiculo = Veiculo.query.get(veiculo_id)
        filtros_aplicados['veiculo_id'] = veiculo.placa if veiculo else veiculo_id
    if combustivel:
        filtros_aplicados['combustivel'] = combustivel
    if setor_filtro:
        filtros_aplicados['setor'] = setor_filtro
    setores = []
    if session.get("usuario_tipo") == "admin":
        setores = [s[0] for s in db.session.query(User.setor).filter(User.setor != None).distinct().order_by(User.setor).all() if s[0]]
    if usuario_tipo != "admin" and usuario_setor:
        veiculos = Veiculo.query.filter(Veiculo.tipo == usuario_setor).order_by(Veiculo.placa).all()
    else:
        veiculos = Veiculo.query.order_by(Veiculo.placa).all()
    agora = datetime.now()
    html = render_template(
        "relatorio_veiculos_print.html",
        dados_veiculos=dados_veiculos,
        veiculos=veiculos,
        tipos_combustivel=TIPOS_COMBUSTIVEL,
        filtros_aplicados=filtros_aplicados,
        setores=setores,
        agora=agora,
        total_registros=len(dados_veiculos)
    )
    pdf = HTML(string=html).write_pdf()
    return Response(pdf, mimetype='application/pdf', headers={"Content-Disposition": "attachment;filename=relatorio_veiculos.pdf"})

@app.route("/relatorios/veiculos/visualizar", endpoint="visualizar_relatorio_veiculos")
def visualizar_relatorio_veiculos():
    if "usuario" not in session:
        return redirect(url_for("login"))
    usuario_tipo = session.get("usuario_tipo")
    usuario_setor = session.get("usuario_setor")
    data_inicio = request.args.get("data_inicio", "")
    data_fim = request.args.get("data_fim", "")
    veiculo_id = request.args.get("veiculo_id", "")
    combustivel = request.args.get("combustivel", "")
    setor_filtro = request.args.get("setor", "")
    query = Abastecimento.query.join(Veiculo).join(Motorista)
    if usuario_tipo == "admin" and setor_filtro:
        query = query.filter(Veiculo.tipo == setor_filtro)
    elif usuario_tipo != "admin" and usuario_setor:
        query = query.filter(Veiculo.tipo == usuario_setor)
    # Filtros de data
    if data_inicio:
        try:
            data_inicio_obj = datetime.fromisoformat(data_inicio)
            query = query.filter(Abastecimento.data >= data_inicio_obj)
        except:
            pass
    if data_fim:
        try:
            data_fim_obj = datetime.fromisoformat(data_fim)
            data_fim_obj = data_fim_obj.replace(hour=23, minute=59, second=59)
            query = query.filter(Abastecimento.data <= data_fim_obj)
        except:
            pass
    # Filtros adicionais
    if veiculo_id:
        try:
            query = query.filter(Abastecimento.veiculo_id == int(veiculo_id))
        except:
            pass
    if combustivel:
        query = query.filter(Veiculo.combustivel == combustivel)
    abastecimentos = query.order_by(Abastecimento.data.desc()).all()
    dados_veiculos = {}
    for abastecimento in abastecimentos:
        veiculo = abastecimento.veiculo
        motorista = abastecimento.motorista
        if veiculo not in dados_veiculos:
            dados_veiculos[veiculo] = {
                'total_litros': 0,
                'total_valor': 0,
                'total_abastecimentos': 0,
                'media_litros': 0,
                'motoristas_mais_utilizados': {},
                'abastecimentos': []
            }
        dados_veiculos[veiculo]['total_litros'] += abastecimento.litros
        dados_veiculos[veiculo]['total_valor'] += abastecimento.valor_total
        dados_veiculos[veiculo]['total_abastecimentos'] += 1
        dados_veiculos[veiculo]['abastecimentos'].append(abastecimento)
        if motorista not in dados_veiculos[veiculo]['motoristas_mais_utilizados']:
            dados_veiculos[veiculo]['motoristas_mais_utilizados'][motorista] = {
                'litros': 0,
                'valor': 0
            }
        dados_veiculos[veiculo]['motoristas_mais_utilizados'][motorista]['litros'] += abastecimento.litros
        dados_veiculos[veiculo]['motoristas_mais_utilizados'][motorista]['valor'] += abastecimento.valor_total
    # Calcular média e preparar dados para gráfico
    for veiculo, dados in dados_veiculos.items():
        if dados['total_abastecimentos'] > 0:
            dados['media_litros'] = dados['total_litros'] / dados['total_abastecimentos']
        else:
            dados['media_litros'] = 0
        motoristas_ordenados = sorted(
            dados['motoristas_mais_utilizados'].items(),
            key=lambda x: x[1]['litros'],
            reverse=True
        )[:5]
        dados['motoristas_mais_utilizados'] = dict(motoristas_ordenados)
        dados['abastecimentos'] = dados['abastecimentos'][:5]
    dados_veiculos = {k: v for k, v in dados_veiculos.items() if v['total_abastecimentos'] > 0}
    if usuario_tipo != "admin" and usuario_setor:
        veiculos = Veiculo.query.filter(Veiculo.tipo == usuario_setor).order_by(Veiculo.placa).all()
    else:
        veiculos = Veiculo.query.order_by(Veiculo.placa).all()
    filtros_aplicados = {}
    if data_inicio:
        filtros_aplicados['data_inicio'] = datetime.fromisoformat(data_inicio).strftime('%d/%m/%Y')
    if data_fim:
        filtros_aplicados['data_fim'] = datetime.fromisoformat(data_fim).strftime('%d/%m/%Y')
    if veiculo_id:
        veiculo = Veiculo.query.get(veiculo_id)
        filtros_aplicados['veiculo_id'] = veiculo.placa if veiculo else veiculo_id
    if combustivel:
        filtros_aplicados['combustivel'] = combustivel
    agora = datetime.now()
    return render_template(
        "relatorio_veiculos_print.html",
        dados_veiculos=dados_veiculos,
        veiculos=veiculos,
        tipos_combustivel=TIPOS_COMBUSTIVEL,
        filtros_aplicados=filtros_aplicados,
        agora=agora,
        total_registros=len(dados_veiculos)
    )

@app.route("/relatorios/motoristas", endpoint="relatorio_motoristas")
def relatorio_motoristas():
    """Página de visualização do relatório por motorista"""
    if "usuario" not in session:
        return redirect(url_for("login"))
    usuario_tipo = session.get("usuario_tipo")
    usuario_setor = session.get("usuario_setor")
    data_inicio = request.args.get("data_inicio", "")
    data_fim = request.args.get("data_fim", "")
    motorista_id = request.args.get("motorista_id", "")
    combustivel = request.args.get("combustivel", "")
    setor_filtro = request.args.get("setor", "")

    query = Abastecimento.query.join(Abastecimento.veiculo).join(Abastecimento.motorista)
    # Filtro por setor (apenas admin pode filtrar por setor)
    if usuario_tipo == "admin" and setor_filtro:
        query = query.filter(Veiculo.tipo == setor_filtro)
    elif usuario_tipo != "admin" and usuario_setor:
        query = query.filter(Veiculo.tipo == usuario_setor)

    # Filtros de data
    if data_inicio:
        try:
            data_inicio_obj = datetime.fromisoformat(data_inicio)
            query = query.filter(Abastecimento.data >= data_inicio_obj)
        except (ValueError, TypeError):
            data_inicio = ""
    if data_fim:
        try:
            data_fim_obj = datetime.fromisoformat(data_fim)
            data_fim_obj = data_fim_obj.replace(hour=23, minute=59, second=59)
            query = query.filter(Abastecimento.data <= data_fim_obj)
        except (ValueError, TypeError):
            data_fim = ""

    # Filtros adicionais
    if motorista_id:
        try:
            query = query.filter(Abastecimento.motorista_id == int(motorista_id))
        except (ValueError, TypeError):
            motorista_id = ""
    if combustivel:
        query = query.filter(Veiculo.combustivel == combustivel)

    abastecimentos = query.order_by(desc(Abastecimento.data)).all()

    # Calcular dados para o relatório
    dados_motoristas = {}
    for abastecimento in abastecimentos:
        motorista = abastecimento.motorista
        veiculo = abastecimento.veiculo
        if not motorista or not veiculo:
            continue
        if motorista not in dados_motoristas:
            dados_motoristas[motorista] = {
                'total_litros': 0,
                'total_valor': 0,
                'total_abastecimentos': 0,
                'media_litros': 0,
                'veiculos_mais_utilizados': {},
                'abastecimentos': []
            }
        dados_motoristas[motorista]['total_litros'] += abastecimento.litros
        dados_motoristas[motorista]['total_valor'] += abastecimento.valor_total
        dados_motoristas[motorista]['total_abastecimentos'] += 1
        dados_motoristas[motorista]['abastecimentos'].append(abastecimento)
        if veiculo not in dados_motoristas[motorista]['veiculos_mais_utilizados']:
            dados_motoristas[motorista]['veiculos_mais_utilizados'][veiculo] = {
                'litros': 0,
                'valor': 0
            }
        dados_motoristas[motorista]['veiculos_mais_utilizados'][veiculo]['litros'] += abastecimento.litros
        dados_motoristas[motorista]['veiculos_mais_utilizados'][veiculo]['valor'] += abastecimento.valor_total

    # Calcular média e preparar dados para gráfico
    for motorista, dados in dados_motoristas.items():
        if dados['total_abastecimentos'] > 0:
            dados['media_litros'] = dados['total_litros'] / dados['total_abastecimentos']
        else:
            dados['media_litros'] = 0
        veiculos_ordenados = sorted(
            dados['veiculos_mais_utilizados'].items(),
            key=lambda x: x[1]['litros'],
            reverse=True
        )[:5]
        dados['veiculos_mais_utilizados'] = dict(veiculos_ordenados)
        dados['abastecimentos'] = dados['abastecimentos'][:5]

    dados_motoristas = {k: v for k, v in dados_motoristas.items() if v['total_abastecimentos'] > 0}

    # Filtros aplicados para badges
    filtros_aplicados = {}
    if usuario_tipo == "admin" and setor_filtro:
        filtros_aplicados["setor"] = setor_filtro
    if data_inicio:
        try:
            filtros_aplicados["data_inicio"] = datetime.fromisoformat(data_inicio).strftime('%d/%m/%Y')
        except Exception:
            filtros_aplicados["data_inicio"] = data_inicio
    if data_fim:
        try:
            filtros_aplicados["data_fim"] = datetime.fromisoformat(data_fim).strftime('%d/%m/%Y')
        except Exception:
            filtros_aplicados["data_fim"] = data_fim
    if motorista_id:
        motorista = Motorista.query.get(motorista_id)
        filtros_aplicados["motorista_id"] = motorista.nome_completo if motorista else motorista_id
    if combustivel:
        filtros_aplicados["combustivel"] = combustivel

    setores = []
    if usuario_tipo == "admin":
        setores = [s[0] for s in db.session.query(User.setor).filter(User.setor != None).distinct().order_by(User.setor).all() if s[0]]

    motoristas_lista = Motorista.query.order_by(Motorista.nome_completo).all()
    agora = datetime.now()
    return render_template(
        "relatorio_motoristas.html",
        dados_motoristas=dados_motoristas,
        motoristas=motoristas_lista,
        tipos_combustivel=TIPOS_COMBUSTIVEL,
        filtros_aplicados=filtros_aplicados,
        setores=setores,
        agora=agora,
        total_registros=len(dados_motoristas)
    )

@app.route("/relatorios/motoristas/csv", endpoint="export_csv_relatorio_motoristas")
def export_csv_relatorio_motoristas():
    """Export CSV do relatório de motoristas - Versão profissional"""
    if "usuario" not in session:
        return redirect(url_for("login"))
    
    usuario_tipo = session.get("usuario_tipo")
    usuario_setor = session.get("usuario_setor")
    data_inicio = request.args.get("data_inicio", "")
    data_fim = request.args.get("data_fim", "")
    motorista_id = request.args.get("motorista_id", "")
    combustivel = request.args.get("combustivel", "")
    setor_filtro = request.args.get("setor", "")

    query = Abastecimento.query.join(Abastecimento.veiculo).join(Abastecimento.motorista)
    
    # Filtro por setor (apenas admin pode filtrar por setor)
    if usuario_tipo == "admin" and setor_filtro:
        query = query.filter(Veiculo.tipo == setor_filtro)
    elif usuario_tipo != "admin" and usuario_setor:
        query = query.filter(Veiculo.tipo == usuario_setor)

    # Filtros de data
    if data_inicio:
        try:
            data_inicio_obj = datetime.fromisoformat(data_inicio)
            query = query.filter(Abastecimento.data >= data_inicio_obj)
        except (ValueError, TypeError):
            data_inicio = ""
    if data_fim:
        try:
            data_fim_obj = datetime.fromisoformat(data_fim)
            data_fim_obj = data_fim_obj.replace(hour=23, minute=59, second=59)
            query = query.filter(Abastecimento.data <= data_fim_obj)
        except (ValueError, TypeError):
            data_fim = ""

    # Filtros adicionais
    if motorista_id:
        try:
            query = query.filter(Abastecimento.motorista_id == int(motorista_id))
        except (ValueError, TypeError):
            motorista_id = ""
    if combustivel:
        query = query.filter(Veiculo.combustivel == combustivel)

    abastecimentos = query.order_by(desc(Abastecimento.data)).all()

    # Calcular dados para o relatório
    dados_motoristas = {}
    for abastecimento in abastecimentos:
        motorista = abastecimento.motorista
        veiculo = abastecimento.veiculo
        
        if motorista not in dados_motoristas:
            dados_motoristas[motorista] = {
                'total_litros': 0,
                'total_valor': 0,
                'total_abastecimentos': 0,
                'media_litros': 0,
                'veiculos_mais_utilizados': {},
                'abastecimentos': []
            }
        
        dados_motoristas[motorista]['total_litros'] += abastecimento.litros
        dados_motoristas[motorista]['total_valor'] += abastecimento.valor_total
        dados_motoristas[motorista]['total_abastecimentos'] += 1
        dados_motoristas[motorista]['abastecimentos'].append(abastecimento)
        
        if veiculo not in dados_motoristas[motorista]['veiculos_mais_utilizados']:
            dados_motoristas[motorista]['veiculos_mais_utilizados'][veiculo] = {
                'litros': 0,
                'valor': 0
            }
        
        dados_motoristas[motorista]['veiculos_mais_utilizados'][veiculo]['litros'] += abastecimento.litros
        dados_motoristas[motorista]['veiculos_mais_utilizados'][veiculo]['valor'] += abastecimento.valor_total

    # Calcular média e preparar dados
    for motorista, dados in dados_motoristas.items():
        if dados['total_abastecimentos'] > 0:
            dados['media_litros'] = dados['total_litros'] / dados['total_abastecimentos']
        else:
            dados['media_litros'] = 0
        
        veiculos_ordenados = sorted(
            dados['veiculos_mais_utilizados'].items(),
            key=lambda x: x[1]['litros'],
            reverse=True
        )[:5]
        dados['veiculos_mais_utilizados'] = dict(veiculos_ordenados)
        dados['abastecimentos'] = dados['abastecimentos'][:5]

    dados_motoristas = {k: v for k, v in dados_motoristas.items() if v['total_abastecimentos'] > 0}

    # Criar CSV profissional
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Cabeçalho profissional com informações do relatório
    writer.writerow(["RELATÓRIO DE MOTORISTAS - SISTEMA DE GESTÃO DE COMBUSTÍVEL"])
    writer.writerow([])
    
    # Informações de filtros e data
    writer.writerow(["Data de geração:", datetime.now().strftime('%d/%m/%Y %H:%M')])
    writer.writerow(["Usuário:", session.get("usuario_nome", "N/A")])
    
    if data_inicio or data_fim:
        periodo = f"{data_inicio} a {data_fim}" if data_inicio and data_fim else data_inicio or data_fim
        writer.writerow(["Período:", periodo])
    
    if setor_filtro:
        writer.writerow(["Setor:", setor_filtro])
    
    if combustivel:
        writer.writerow(["Combustível:", combustivel])
    
    if motorista_id:
        motorista_especifico = Motorista.query.get(motorista_id)
        if motorista_especifico:
            writer.writerow(["Motorista específico:", motorista_especifico.nome_completo])
    
    writer.writerow(["Total de motoristas:", len(dados_motoristas)])
    writer.writerow([])
    writer.writerow([])
    
    # Cabeçalho da tabela principal
    writer.writerow([
        "MOTORISTA",
        "DOCUMENTO",
        "TOTAL LITROS",
        "VALOR TOTAL (R$)",
        "MÉDIA LITROS/ABAST.",
        "TOTAL ABASTECIMENTOS",
        "VALOR MÉDIO POR ABAST. (R$)"
    ])
    
    # Dados formatados - ordenar por total de litros (maior primeiro)
    for motorista, dados in sorted(
        dados_motoristas.items(), 
        key=lambda x: x[1]['total_litros'], 
        reverse=True
    ):
        valor_medio_abast = dados['total_valor'] / dados['total_abastecimentos'] if dados['total_abastecimentos'] > 0 else 0
        
        writer.writerow([
            motorista.nome_completo if motorista else "N/A",
            motorista.documento if motorista and motorista.documento else "N/A",
            f"{dados['total_litros']:.2f} L",
            f"R$ {dados['total_valor']:.2f}",
            f"{dados['media_litros']:.2f} L",
            dados['total_abastecimentos'],
            f"R$ {valor_medio_abast:.2f}"
        ])
    
    writer.writerow([])
    
    # Totais gerais
    total_geral_litros = sum(dados['total_litros'] for dados in dados_motoristas.values())
    total_geral_valor = sum(dados['total_valor'] for dados in dados_motoristas.values())
    total_geral_abastecimentos = sum(dados['total_abastecimentos'] for dados in dados_motoristas.values())
    valor_medio_geral = total_geral_valor / total_geral_abastecimentos if total_geral_abastecimentos > 0 else 0
    
    writer.writerow(["TOTAIS GERAIS:"])
    writer.writerow([
        "",
        "",
        f"{total_geral_litros:.2f} L",
        f"R$ {total_geral_valor:.2f}",
        f"{(total_geral_litros / len(dados_motoristas)):.2f} L" if dados_motoristas else "0.00 L",
        total_geral_abastecimentos,
        f"R$ {valor_medio_geral:.2f}"
    ])
    
    writer.writerow([])
    writer.writerow([])
    
    # Detalhamento por veículo (top 3 por motorista)
    writer.writerow(["DETALHAMENTO POR VEÍCULO (TOP 3 POR MOTORISTA)"])
    writer.writerow([])
    writer.writerow(["MOTORISTA", "VEÍCULO", "PLACA", "COMBUSTÍVEL", "TOTAL LITROS", "VALOR TOTAL (R$)", "% DO TOTAL"])
    
    for motorista, dados in sorted(
        dados_motoristas.items(), 
        key=lambda x: x[1]['total_litros'], 
        reverse=True
    ):
        motorista_total_litros = dados['total_litros']
        
        for i, (veiculo, info) in enumerate(list(dados['veiculos_mais_utilizados'].items())[:3]):
            percentual = (info['litros'] / motorista_total_litros * 100) if motorista_total_litros > 0 else 0
            
            writer.writerow([
                motorista.nome_completo if i == 0 else "",  # Evitar repetição do nome
                veiculo.placa if veiculo else "N/A",
                veiculo.placa if veiculo else "N/A",
                veiculo.combustivel if veiculo else "N/A",
                f"{info['litros']:.2f} L",
                f"R$ {info['valor']:.2f}",
                f"{percentual:.1f}%"
            ])
        
        # Adicionar linha de subtotal do motorista
        if dados['veiculos_mais_utilizados']:
            writer.writerow([
                "Subtotal:",
                "",
                "",
                "",
                f"{motorista_total_litros:.2f} L",
                f"R$ {dados['total_valor']:.2f}",
                "100.0%"
            ])
        
        writer.writerow([])  # Linha em branco entre motoristas
    
    writer.writerow([])
    
    # Estatísticas adicionais
    writer.writerow(["ESTATÍSTICAS ADICIONAIS"])
    writer.writerow([])
    
    if dados_motoristas:
        # Motorista com maior consumo
        maior_consumo = max(dados_motoristas.items(), key=lambda x: x[1]['total_litros'])
        writer.writerow(["Motorista com maior consumo:", maior_consumo[0].nome_completo if maior_consumo[0] else "N/A"])
        writer.writerow(["Total do maior consumo:", f"{maior_consumo[1]['total_litros']:.2f} L"])
        
        # Médias gerais
        media_litros_por_motorista = total_geral_litros / len(dados_motoristas)
        media_abastecimentos_por_motorista = total_geral_abastecimentos / len(dados_motoristas)
        
        writer.writerow(["Média de litros por motorista:", f"{media_litros_por_motorista:.2f} L"])
        writer.writerow(["Média de abastecimentos por motorista:", f"{media_abastecimentos_por_motorista:.1f}"])
        writer.writerow(["Valor médio por abastecimento:", f"R$ {valor_medio_geral:.2f}"])
    
    output.seek(0)
    
    # Nome do arquivo com data
    data_arquivo = datetime.now().strftime('%Y%m%d_%H%M')
    filename = f"relatorio_motoristas_{data_arquivo}.csv"
    
    return Response(
        output.getvalue(),
        mimetype='text/csv; charset=utf-8',
        headers={
            "Content-Disposition": f"attachment;filename={filename}",
            "Content-Type": "text/csv; charset=utf-8"
        }
    )
    
@app.route("/relatorios/motoristas/pdf", endpoint="export_pdf_relatorio_motoristas")
def export_pdf_relatorio_motoristas():
    """Export PDF do relatório de motoristas"""
    if "usuario" not in session:
        return redirect(url_for("login"))
    usuario_tipo = session.get("usuario_tipo")
    usuario_setor = session.get("usuario_setor")
    data_inicio = request.args.get("data_inicio", "")
    data_fim = request.args.get("data_fim", "")
    motorista_id = request.args.get("motorista_id", "")
    combustivel = request.args.get("combustivel", "")
    setor_filtro = request.args.get("setor", "")

    query = Abastecimento.query.join(Abastecimento.veiculo).join(Abastecimento.motorista)
    # Filtro por setor (apenas admin pode filtrar por setor)
    if usuario_tipo == "admin" and setor_filtro:
        query = query.filter(Veiculo.tipo == setor_filtro)
    elif usuario_tipo != "admin" and usuario_setor:
        query = query.filter(Veiculo.tipo == usuario_setor)

    # Filtros de data
    if data_inicio:
        try:
            data_inicio_obj = datetime.fromisoformat(data_inicio)
            query = query.filter(Abastecimento.data >= data_inicio_obj)
        except (ValueError, TypeError):
            data_inicio = ""
    if data_fim:
        try:
            data_fim_obj = datetime.fromisoformat(data_fim)
            data_fim_obj = data_fim_obj.replace(hour=23, minute=59, second=59)
            query = query.filter(Abastecimento.data <= data_fim_obj)
        except (ValueError, TypeError):
            data_fim = ""

    # Filtros adicionais
    if motorista_id:
        try:
            query = query.filter(Abastecimento.motorista_id == int(motorista_id))
        except (ValueError, TypeError):
            motorista_id = ""
    if combustivel:
        query = query.filter(Veiculo.combustivel == combustivel)

    abastecimentos = query.order_by(desc(Abastecimento.data)).all()

    # Calcular dados para o relatório
    dados_motoristas = {}
    for abastecimento in abastecimentos:
        motorista = abastecimento.motorista
        veiculo = abastecimento.veiculo
        if motorista not in dados_motoristas:
            dados_motoristas[motorista] = {
                'total_litros': 0,
                'total_valor': 0,
                'total_abastecimentos': 0,
                'media_litros': 0,
                'veiculos_mais_utilizados': {},
                'abastecimentos': []
            }
        dados_motoristas[motorista]['total_litros'] += abastecimento.litros
        dados_motoristas[motorista]['total_valor'] += abastecimento.valor_total
        dados_motoristas[motorista]['total_abastecimentos'] += 1
        dados_motoristas[motorista]['abastecimentos'].append(abastecimento)
        if veiculo not in dados_motoristas[motorista]['veiculos_mais_utilizados']:
            dados_motoristas[motorista]['veiculos_mais_utilizados'][veiculo] = {
                'litros': 0,
                'valor': 0
            }
        dados_motoristas[motorista]['veiculos_mais_utilizados'][veiculo]['litros'] += abastecimento.litros
        dados_motoristas[motorista]['veiculos_mais_utilizados'][veiculo]['valor'] += abastecimento.valor_total

    # Calcular média e preparar dados para gráfico
    for motorista, dados in dados_motoristas.items():
        if dados['total_abastecimentos'] > 0:
            dados['media_litros'] = dados['total_litros'] / dados['total_abastecimentos']
        else:
            dados['media_litros'] = 0
        veiculos_ordenados = sorted(
            dados['veiculos_mais_utilizados'].items(),
            key=lambda x: x[1]['litros'],
            reverse=True
        )[:5]
        dados['veiculos_mais_utilizados'] = dict(veiculos_ordenados)
        dados['abastecimentos'] = dados['abastecimentos'][:5]

    dados_motoristas = {k: v for k, v in dados_motoristas.items() if v['total_abastecimentos'] > 0}

    # Filtros aplicados para badges
    filtros_aplicados = {}
    if usuario_tipo == "admin" and setor_filtro:
        filtros_aplicados["setor"] = setor_filtro
    if data_inicio:
        try:
            filtros_aplicados["data_inicio"] = datetime.fromisoformat(data_inicio).strftime('%d/%m/%Y')
        except Exception:
            filtros_aplicados["data_inicio"] = data_inicio
    if data_fim:
        try:
            filtros_aplicados["data_fim"] = datetime.fromisoformat(data_fim).strftime('%d/%m/%Y')
        except Exception:
            filtros_aplicados["data_fim"] = data_fim
    if motorista_id:
        motorista = Motorista.query.get(motorista_id)
        filtros_aplicados["motorista_id"] = motorista.nome_completo if motorista else motorista_id
    if combustivel:
        filtros_aplicados["combustivel"] = combustivel

    setores = []
    if usuario_tipo == "admin":
        setores = [s[0] for s in db.session.query(User.setor).filter(User.setor != None).distinct().order_by(User.setor).all() if s[0]]

    motoristas_lista = Motorista.query.order_by(Motorista.nome_completo).all()
    agora = datetime.now()
    html = render_template(
        "relatorio_motoristas_print.html",
        dados_motoristas=dados_motoristas,
        motoristas=motoristas_lista,
        tipos_combustivel=TIPOS_COMBUSTIVEL,
        filtros_aplicados=filtros_aplicados,
        setores=setores,
        agora=agora,
        total_registros=len(dados_motoristas)
    )
    pdf = HTML(string=html).write_pdf()
    return Response(pdf, mimetype='application/pdf', headers={"Content-Disposition": "attachment;filename=relatorio_motoristas.pdf"})

@app.route("/relatorios/motoristas/visualizar", endpoint="visualizar_relatorio_motoristas")
def visualizar_relatorio_motoristas():
    if "usuario" not in session:
        return redirect(url_for("login"))
    usuario_tipo = session.get("usuario_tipo")
    usuario_setor = session.get("usuario_setor")
    if usuario_tipo != "admin" and usuario_setor:
        motoristas = Motorista.query.join(Abastecimento).join(Veiculo).filter(Veiculo.tipo == usuario_setor).order_by(Motorista.nome_completo).distinct().all()
    else:
        motoristas = Motorista.query.order_by(Motorista.nome_completo).all()
    agora = datetime.now()
    return render_template(
        "motoristas_print.html",
        motoristas=motoristas,
        agora=agora
    )

@app.route("/relatorios/abastecimentos", endpoint="relatorio_abastecimentos")
def relatorio_abastecimentos():
    """Página de visualização do relatório de abastecimentos"""
    if "usuario" not in session:
        return redirect(url_for("login"))
    usuario_tipo = session.get("usuario_tipo")
    usuario_setor = session.get("usuario_setor")
    data_inicio = request.args.get("data_inicio", "")
    data_fim = request.args.get("data_fim", "")
    veiculo_id = request.args.get("veiculo_id", "")
    motorista_id = request.args.get("motorista_id", "")
    combustivel = request.args.get("combustivel", "")
    min_litros = request.args.get("min_litros", "")
    max_litros = request.args.get("max_litros", "")
    setor_filtro = request.args.get("setor", "")

    query = Abastecimento.query.join(Veiculo).join(Motorista)
    # Filtro por setor (apenas admin pode filtrar por setor)
    if usuario_tipo == "admin" and setor_filtro:
        query = query.filter(Veiculo.tipo == setor_filtro)
    elif usuario_tipo != "admin" and usuario_setor:
        query = query.filter(Veiculo.tipo == usuario_setor)

    # Filtros de data
    if data_inicio:
        try:
            data_inicio_obj = datetime.fromisoformat(data_inicio)
            query = query.filter(Abastecimento.data >= data_inicio_obj)
        except Exception:
            pass
    if data_fim:
        try:
            data_fim_obj = datetime.fromisoformat(data_fim)
            data_fim_obj = data_fim_obj.replace(hour=23, minute=59, second=59)
            query = query.filter(Abastecimento.data <= data_fim_obj)
        except Exception:
            pass
    if veiculo_id:
        try:
            query = query.filter(Abastecimento.veiculo_id == int(veiculo_id))
        except Exception:
            pass
    if motorista_id:
        try:
            query = query.filter(Abastecimento.motorista_id == int(motorista_id))
        except Exception:
            pass
    if combustivel:
        query = query.filter(Veiculo.combustivel == combustivel)
    if min_litros:
        try:
            query = query.filter(Abastecimento.litros >= float(min_litros))
        except Exception:
            pass
    if max_litros:
        try:
            query = query.filter(Abastecimento.litros <= float(max_litros))
        except Exception:
            pass

    abastecimentos = query.order_by(Abastecimento.data.desc()).all()
    total_litros = sum(a.litros for a in abastecimentos) if abastecimentos else 0
    valor_total = sum(a.valor_total for a in abastecimentos) if abastecimentos else 0
    media_litros = total_litros / len(abastecimentos) if abastecimentos else 0

    # Listas para selects
    veiculos = Veiculo.query.order_by(Veiculo.placa).all()
    motoristas = Motorista.query.order_by(Motorista.nome_completo).all()
    setores = []
    if usuario_tipo == "admin":
        setores = [s[0] for s in db.session.query(User.setor).filter(User.setor != None).distinct().order_by(User.setor).all() if s[0]]

    # Filtros aplicados para badges
    filtros_aplicados = {}
    if usuario_tipo == "admin" and setor_filtro:
        filtros_aplicados["setor"] = setor_filtro
    if data_inicio:
        try:
            filtros_aplicados["data_inicio"] = datetime.fromisoformat(data_inicio).strftime('%d/%m/%Y')
        except Exception:
            filtros_aplicados["data_inicio"] = data_inicio
    if data_fim:
        try:
            filtros_aplicados["data_fim"] = datetime.fromisoformat(data_fim).strftime('%d/%m/%Y')
        except Exception:
            filtros_aplicados["data_fim"] = data_fim
    if veiculo_id:
        veiculo = Veiculo.query.get(veiculo_id)
        filtros_aplicados["veiculo_id"] = veiculo.placa if veiculo else veiculo_id
    if motorista_id:
        motorista = Motorista.query.get(motorista_id)
        filtros_aplicados["motorista_id"] = motorista.nome_completo if motorista else motorista_id
    if combustivel:
        filtros_aplicados["combustivel"] = combustivel
    if min_litros:
        filtros_aplicados["min_litros"] = min_litros
    if max_litros:
        filtros_aplicados["max_litros"] = max_litros

    agora = datetime.now()
    # O template espera a variável 'dados', não 'abastecimentos'
    return render_template(
        "relatorio_abastecimentos.html",
        dados=abastecimentos,
        veiculos=veiculos,
        motoristas=motoristas,
        tipos_combustivel=TIPOS_COMBUSTIVEL,
        filtros_aplicados=filtros_aplicados,
        total_registros=len(abastecimentos),
        setores=setores,
        agora=agora,
        total_litros=total_litros,
        valor_total=valor_total,
        media_litros=media_litros
    )

@app.route("/relatorios/abastecimentos/csv", endpoint="export_csv_relatorio_abastecimentos")
def relatorio_abastecimentos_csv():
    """Export CSV do relatório de abastecimentos - Versão profissional"""
    if "usuario" not in session:
        return redirect(url_for("login"))
    
    usuario_tipo = session.get("usuario_tipo")
    usuario_setor = session.get("usuario_setor")
    data_inicio = request.args.get("data_inicio", "")
    data_fim = request.args.get("data_fim", "")
    veiculo_id = request.args.get("veiculo_id", "")
    motorista_id = request.args.get("motorista_id", "")
    combustivel = request.args.get("combustivel", "")
    min_litros = request.args.get("min_litros", "")
    max_litros = request.args.get("max_litros", "")
    setor_filtro = request.args.get("setor", "")

    query = Abastecimento.query.join(Veiculo).join(Motorista)
    
    # Filtro por setor (apenas admin pode filtrar por setor)
    if usuario_tipo == "admin" and setor_filtro:
        query = query.filter(Veiculo.tipo == setor_filtro)
    elif usuario_tipo != "admin" and usuario_setor:
        query = query.filter(Veiculo.tipo == usuario_setor)

    # Filtros de data
    if data_inicio:
        try:
            data_inicio_obj = datetime.fromisoformat(data_inicio)
            query = query.filter(Abastecimento.data >= data_inicio_obj)
        except Exception:
            pass
    if data_fim:
        try:
            data_fim_obj = datetime.fromisoformat(data_fim)
            data_fim_obj = data_fim_obj.replace(hour=23, minute=59, second=59)
            query = query.filter(Abastecimento.data <= data_fim_obj)
        except Exception:
            pass
    
    # Filtros adicionais
    if veiculo_id:
        try:
            query = query.filter(Abastecimento.veiculo_id == int(veiculo_id))
        except Exception:
            pass
    if motorista_id:
        try:
            query = query.filter(Abastecimento.motorista_id == int(motorista_id))
        except Exception:
            pass
    if combustivel:
        query = query.filter(Veiculo.combustivel == combustivel)
    if min_litros:
        try:
            query = query.filter(Abastecimento.litros >= float(min_litros))
        except Exception:
            pass
    if max_litros:
        try:
            query = query.filter(Abastecimento.litros <= float(max_litros))
        except Exception:
            pass

    abastecimentos = query.order_by(Abastecimento.data.desc()).all()

    # Criar CSV profissional
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Cabeçalho profissional com informações do relatório
    writer.writerow(["RELATÓRIO DETALHADO DE ABASTECIMENTOS - SISTEMA DE GESTÃO DE COMBUSTÍVEL"])
    writer.writerow([])
    
    # Informações de filtros e data
    writer.writerow(["Data de geração:", datetime.now().strftime('%d/%m/%Y %H:%M')])
    writer.writerow(["Usuário:", session.get("usuario_nome", "N/A")])
    
    if data_inicio or data_fim:
        periodo = f"{data_inicio} a {data_fim}" if data_inicio and data_fim else data_inicio or data_fim
        writer.writerow(["Período:", periodo])
    
    if setor_filtro:
        writer.writerow(["Setor:", setor_filtro])
    
    if veiculo_id:
        veiculo_especifico = Veiculo.query.get(veiculo_id)
        if veiculo_especifico:
            writer.writerow(["Veículo específico:", f"{veiculo_especifico.placa} ({veiculo_especifico.tipo})"])
    
    if motorista_id:
        motorista_especifico = Motorista.query.get(motorista_id)
        if motorista_especifico:
            writer.writerow(["Motorista específico:", motorista_especifico.nome_completo])
    
    if combustivel:
        writer.writerow(["Combustível:", combustivel])
    
    if min_litros or max_litros:
        faixa_litros = ""
        if min_litros and max_litros:
            faixa_litros = f"{min_litros} a {max_litros} litros"
        elif min_litros:
            faixa_litros = f"Mínimo {min_litros} litros"
        elif max_litros:
            faixa_litros = f"Máximo {max_litros} litros"
        writer.writerow(["Faixa de litros:", faixa_litros])
    
    writer.writerow(["Total de registros:", len(abastecimentos)])
    writer.writerow([])
    writer.writerow([])
    
    # Cabeçalho da tabela principal
    writer.writerow([
        "DATA",
        "HORA", 
        "VEÍCULO",
        "PLACA",
        "TIPO VEÍCULO",
        "COMBUSTÍVEL",
        "MOTORISTA",
        "DOCUMENTO MOTORISTA",
        "HODÔMETRO (km)",
        "LITROS",
        "VALOR UNITÁRIO (R$)",
        "VALOR TOTAL (R$)",
        "Nº NOTA FISCAL",
        "CONTRATO",
        "OBSERVAÇÕES"
    ])
    
    # Calcular totais
    total_litros = 0
    total_valor = 0
    
    # Dados formatados
    for abastecimento in abastecimentos:
        # Calcular valor unitário
        valor_unitario = abastecimento.valor_total / abastecimento.litros if abastecimento.litros > 0 else 0
        
        # Acumular totais
        total_litros += abastecimento.litros
        total_valor += abastecimento.valor_total
        
        # Obter informações do contrato se existir
        contrato_info = ""
        if abastecimento.contrato_id:
            contrato = ContratoCombustivel.query.get(abastecimento.contrato_id)
            if contrato:
                contrato_info = f"{contrato.numero_contrato}/{contrato.ano_contrato}"
        
        writer.writerow([
            abastecimento.data.strftime('%d/%m/%Y'),
            abastecimento.data.strftime('%H:%M'),
            abastecimento.veiculo.placa if abastecimento.veiculo else "N/A",
            abastecimento.veiculo.placa if abastecimento.veiculo else "N/A",
            abastecimento.veiculo.tipo if abastecimento.veiculo else "N/A",
            abastecimento.veiculo.combustivel if abastecimento.veiculo else "N/A",
            abastecimento.motorista.nome_completo if abastecimento.motorista else "N/A",
            abastecimento.motorista.documento if abastecimento.motorista and abastecimento.motorista.documento else "N/A",
            f"{abastecimento.hodometro:,}".replace(",", "."),
            f"{abastecimento.litros:.2f}",
            f"R$ {valor_unitario:.3f}",
            f"R$ {abastecimento.valor_total:.2f}",
            abastecimento.numero_nota or "N/A",
            contrato_info,
            abastecimento.observacoes or ""
        ])
    
    writer.writerow([])
    
    # Linha de totais
    writer.writerow(["TOTAIS GERAIS:"])
    writer.writerow([
        "", "", "", "", "", "", "", "", "",
        f"{total_litros:.2f} L",
        "",
        f"R$ {total_valor:.2f}",
        "", "", ""
    ])
    
    # Calcular médias e estatísticas
    if abastecimentos:
        media_litros = total_litros / len(abastecimentos)
        media_valor = total_valor / len(abastecimentos)
        valor_medio_litro = total_valor / total_litros if total_litros > 0 else 0
        
        writer.writerow(["MÉDIAS E ESTATÍSTICAS:"])
        writer.writerow([
            "", "", "", "", "", "", "", "", "",
            f"{media_litros:.2f} L/abast.",
            f"R$ {valor_medio_litro:.3f}/L",
            f"R$ {media_valor:.2f}/abast.",
            "", "", ""
        ])
    
    writer.writerow([])
    writer.writerow([])
    
    # Resumo por veículo
    if len(abastecimentos) > 1:  # Só mostrar se houver mais de um registro
        writer.writerow(["RESUMO POR VEÍCULO"])
        writer.writerow([])
        writer.writerow(["VEÍCULO", "TOTAL ABASTECIMENTOS", "TOTAL LITROS", "VALOR TOTAL (R$)", "% DO TOTAL"])
        
        resumo_veiculos = {}
        for abastecimento in abastecimentos:
            veiculo = abastecimento.veiculo
            if veiculo not in resumo_veiculos:
                resumo_veiculos[veiculo] = {'count': 0, 'litros': 0, 'valor': 0}
            
            resumo_veiculos[veiculo]['count'] += 1
            resumo_veiculos[veiculo]['litros'] += abastecimento.litros
            resumo_veiculos[veiculo]['valor'] += abastecimento.valor_total
        
        for veiculo, dados in sorted(resumo_veiculos.items(), key=lambda x: x[1]['litros'], reverse=True):
            percentual_litros = (dados['litros'] / total_litros * 100) if total_litros > 0 else 0
            
            writer.writerow([
                veiculo.placa if veiculo else "N/A",
                dados['count'],
                f"{dados['litros']:.2f} L",
                f"R$ {dados['valor']:.2f}",
                f"{percentual_litros:.1f}%"
            ])
        
        writer.writerow([])
    
    # Resumo por motorista
    if len(abastecimentos) > 1:  # Só mostrar se houver mais de um registro
        writer.writerow(["RESUMO POR MOTORISTA"])
        writer.writerow([])
        writer.writerow(["MOTORISTA", "TOTAL ABASTECIMENTOS", "TOTAL LITROS", "VALOR TOTAL (R$)", "% DO TOTAL"])
        
        resumo_motoristas = {}
        for abastecimento in abastecimentos:
            motorista = abastecimento.motorista
            if motorista not in resumo_motoristas:
                resumo_motoristas[motorista] = {'count': 0, 'litros': 0, 'valor': 0}
            
            resumo_motoristas[motorista]['count'] += 1
            resumo_motoristas[motorista]['litros'] += abastecimento.litros
            resumo_motoristas[motorista]['valor'] += abastecimento.valor_total
        
        for motorista, dados in sorted(resumo_motoristas.items(), key=lambda x: x[1]['litros'], reverse=True):
            percentual_litros = (dados['litros'] / total_litros * 100) if total_litros > 0 else 0
            
            writer.writerow([
                motorista.nome_completo if motorista else "N/A",
                dados['count'],
                f"{dados['litros']:.2f} L",
                f"R$ {dados['valor']:.2f}",
                f"{percentual_litros:.1f}%"
            ])
    
    writer.writerow([])
    writer.writerow([])
    
    # Informações adicionais
    writer.writerow(["INFORMAÇÕES ADICIONAIS"])
    writer.writerow([])
    writer.writerow(["Maior abastecimento (litros):", f"{max(a.litros for a in abastecimentos):.2f} L" if abastecimentos else "N/A"])
    writer.writerow(["Menor abastecimento (litros):", f"{min(a.litros for a in abastecimentos):.2f} L" if abastecimentos else "N/A"])
    writer.writerow(["Valor médio por litro:", f"R$ {valor_medio_litro:.3f}" if abastecimentos else "N/A"])
    
    output.seek(0)
    
    # Nome do arquivo com data
    data_arquivo = datetime.now().strftime('%Y%m%d_%H%M')
    filename = f"relatorio_abastecimentos_detalhado_{data_arquivo}.csv"
    
    return Response(
        output.getvalue(),
        mimetype='text/csv; charset=utf-8',
        headers={
            "Content-Disposition": f"attachment;filename={filename}",
            "Content-Type": "text/csv; charset=utf-8"
        }
    )
    
@app.route("/relatorio-contratos", endpoint="relatorio_contratos")
def relatorio_contratos():
    if "usuario" not in session:
        return redirect(url_for("login"))
    contratos = ContratoCombustivel.query.filter_by(ativo=True).order_by(ContratoCombustivel.data_inicio_contrato).all()
    dados_relatorio = []
    for contrato in contratos:
        for item in contrato.itens:
            inicio_contrato = datetime.combine(contrato.data_inicio_contrato, datetime.min.time())
            fim_contrato = datetime.combine(contrato.data_fim_contrato, datetime.max.time())
            abastecimentos_tipo = Abastecimento.query.join(Veiculo).filter(
                Veiculo.combustivel == item.tipo_combustivel,
                Abastecimento.data >= inicio_contrato,
                Abastecimento.data <= fim_contrato
            ).all()
            abastecimentos_contrato = Abastecimento.query.filter(
                Abastecimento.contrato_id == contrato.id,
                Abastecimento.data >= inicio_contrato,
                Abastecimento.data <= fim_contrato
            ).all()
            todos_abastecimentos = list(set(abastecimentos_tipo + abastecimentos_contrato))
            quantidade_consumida = sum(a.litros for a in todos_abastecimentos)
            valor_usado = sum(a.valor_total for a in todos_abastecimentos)
            quantidade_restante = max(0, item.quantidade - quantidade_consumida)
            valor_restante = max(0, item.valor_total - valor_usado)
            percentual_consumido = (quantidade_consumida / item.quantidade * 100) if item.quantidade > 0 else 0
            dados_relatorio.append({
                'tipo_combustivel': item.tipo_combustivel,
                'fornecedor': contrato.fornecedor,
                'numero_contrato': contrato.numero_contrato,
                'ano_contrato': contrato.ano_contrato,
                'data_inicio_contrato': contrato.data_inicio_contrato,
                'data_fim_contrato': contrato.data_fim_contrato,
                'quantidade_contratada': item.quantidade,
                'valor_total': item.valor_total,
                'valor_por_litro': item.valor_por_litro,
                'quantidade_consumida': quantidade_consumida,
                'valor_usado': valor_usado,
                'quantidade_restante': quantidade_restante,
                'valor_restante': valor_restante,
                'percentual_consumido': percentual_consumido
            })
    agora = datetime.now()
    is_admin = session.get("usuario_tipo") == "admin"
    return render_template(
        "relatorio_contratos.html",
        dados=dados_relatorio,
        contratos=contratos,
        agora=agora,
        is_admin=is_admin
    )

@app.route("/relatorios/contratos/visualizar", endpoint="visualizar_relatorio_contratos")
def visualizar_relatorio_contratos():
    if "usuario" not in session:
        return redirect(url_for("login"))
    contratos = ContratoCombustivel.query.filter_by(ativo=True).order_by(ContratoCombustivel.data_inicio_contrato).all()
    dados_relatorio = []
    for contrato in contratos:
        for item in contrato.itens:
            inicio_contrato = datetime.combine(contrato.data_inicio_contrato, datetime.min.time())
            fim_contrato = datetime.combine(contrato.data_fim_contrato, datetime.max.time())
            abastecimentos_tipo = Abastecimento.query.join(Veiculo).filter(
                Veiculo.combustivel == item.tipo_combustivel,
                Abastecimento.data >= inicio_contrato,
                Abastecimento.data <= fim_contrato
            ).all()
            abastecimentos_contrato = Abastecimento.query.filter(
                Abastecimento.contrato_id == contrato.id,
                Abastecimento.data >= inicio_contrato,
                Abastecimento.data <= fim_contrato
            ).all()
            todos_abastecimentos = list(set(abastecimentos_tipo + abastecimentos_contrato))
            quantidade_consumida = sum(a.litros for a in todos_abastecimentos)
            valor_usado = sum(a.valor_total for a in todos_abastecimentos)
            quantidade_restante = max(0, item.quantidade - quantidade_consumida)
            valor_restante = max(0, item.valor_total - valor_usado)
            percentual_consumido = (quantidade_consumida / item.quantidade * 100) if item.quantidade > 0 else 0
            dados_relatorio.append({
                'tipo_combustivel': item.tipo_combustivel,
                'fornecedor': contrato.fornecedor,
                'numero_contrato': contrato.numero_contrato,
                'ano_contrato': contrato.ano_contrato,
                'data_inicio_contrato': contrato.data_inicio_contrato,
                'data_fim_contrato': contrato.data_fim_contrato,
                'quantidade_contratada': item.quantidade,
                'valor_total': item.valor_total,
                'valor_por_litro': item.valor_por_litro,
                'quantidade_consumida': quantidade_consumida,
                'valor_usado': valor_usado,
                'quantidade_restante': quantidade_restante,
                'valor_restante': valor_restante,
                'percentual_consumido': percentual_consumido
            })
    agora = datetime.now()
    is_admin = session.get("usuario_tipo") == "admin"
    return render_template(
        "relatorio_contratos_print.html",
        dados=dados_relatorio,
        contratos=contratos,
        agora=agora,
        is_admin=is_admin
    )
# ...existing code...

# ----------------------

# Rota de visualização/print do relatório de abastecimentos
@app.route("/relatorios/abastecimentos/visualizar", endpoint="visualizar_relatorio_abastecimentos")
def visualizar_relatorio_abastecimentos():
    if "usuario" not in session:
        return redirect(url_for("login"))
    usuario_tipo = session.get("usuario_tipo")
    usuario_setor = session.get("usuario_setor")
    data_inicio = request.args.get("data_inicio", "")
    data_fim = request.args.get("data_fim", "")
    veiculo_id = request.args.get("veiculo_id", "")
    motorista_id = request.args.get("motorista_id", "")
    combustivel = request.args.get("combustivel", "")
    min_litros = request.args.get("min_litros", "")
    max_litros = request.args.get("max_litros", "")
    setor_filtro = request.args.get("setor", "")

    query = Abastecimento.query.join(Veiculo).join(Motorista)
    if usuario_tipo == "admin" and setor_filtro:
        query = query.filter(Veiculo.tipo == setor_filtro)
    elif usuario_tipo != "admin" and usuario_setor:
        query = query.filter(Veiculo.tipo == usuario_setor)
    if data_inicio:
        try:
            data_inicio_obj = datetime.fromisoformat(data_inicio)
            query = query.filter(Abastecimento.data >= data_inicio_obj)
        except Exception:
            pass
    if data_fim:
        try:
            data_fim_obj = datetime.fromisoformat(data_fim)
            data_fim_obj = data_fim_obj.replace(hour=23, minute=59, second=59)
            query = query.filter(Abastecimento.data <= data_fim_obj)
        except Exception:
            pass
    if veiculo_id:
        try:
            query = query.filter(Abastecimento.veiculo_id == int(veiculo_id))
        except Exception:
            pass
    if motorista_id:
        try:
            query = query.filter(Abastecimento.motorista_id == int(motorista_id))
        except Exception:
            pass
    if combustivel:
        query = query.filter(Veiculo.combustivel == combustivel)
    if min_litros:
        try:
            query = query.filter(Abastecimento.litros >= float(min_litros))
        except Exception:
            pass
    if max_litros:
        try:
            query = query.filter(Abastecimento.litros <= float(max_litros))
        except Exception:
            pass

    abastecimentos = query.order_by(Abastecimento.data.desc()).all()
    total_litros = sum(a.litros for a in abastecimentos) if abastecimentos else 0
    valor_total = sum(a.valor_total for a in abastecimentos) if abastecimentos else 0
    media_litros = total_litros / len(abastecimentos) if abastecimentos else 0

    veiculos = Veiculo.query.order_by(Veiculo.placa).all()
    motoristas = Motorista.query.order_by(Motorista.nome_completo).all()
    setores = []
    if usuario_tipo == "admin":
        setores = [s[0] for s in db.session.query(User.setor).filter(User.setor != None).distinct().order_by(User.setor).all() if s[0]]

    filtros_aplicados = {}
    if usuario_tipo == "admin" and setor_filtro:
        filtros_aplicados["setor"] = setor_filtro
    if data_inicio:
        try:
            filtros_aplicados["data_inicio"] = datetime.fromisoformat(data_inicio).strftime('%d/%m/%Y')
        except Exception:
            filtros_aplicados["data_inicio"] = data_inicio
    if data_fim:
        try:
            filtros_aplicados["data_fim"] = datetime.fromisoformat(data_fim).strftime('%d/%m/%Y')
        except Exception:
            filtros_aplicados["data_fim"] = data_fim
    if veiculo_id:
        veiculo = Veiculo.query.get(veiculo_id)
        filtros_aplicados["veiculo_id"] = veiculo.placa if veiculo else veiculo_id
    if motorista_id:
        motorista = Motorista.query.get(motorista_id)
        filtros_aplicados["motorista_id"] = motorista.nome_completo if motorista else motorista_id
    if combustivel:
        filtros_aplicados["combustivel"] = combustivel
    if min_litros:
        filtros_aplicados["min_litros"] = min_litros
    if max_litros:
        filtros_aplicados["max_litros"] = max_litros

    agora = datetime.now()
    return render_template(
        "relatorio_abastecimentos_print.html",
        abastecimentos=abastecimentos,
        veiculos=veiculos,
        motoristas=motoristas,
        tipos_combustivel=TIPOS_COMBUSTIVEL,
        filtros_aplicados=filtros_aplicados,
        total_registros=len(abastecimentos),
        setores=setores,
        agora=agora,
        total_litros=total_litros,
        valor_total=valor_total,
        media_litros=media_litros
    )

@app.route("/relatorios/abastecimentos/pdf", endpoint="export_pdf_relatorio_abastecimentos")
def export_pdf_relatorio_abastecimentos():
    if "usuario" not in session:
        return redirect(url_for("login"))
    usuario_tipo = session.get("usuario_tipo")
    usuario_setor = session.get("usuario_setor")
    data_inicio = request.args.get("data_inicio", "")
    data_fim = request.args.get("data_fim", "")
    veiculo_id = request.args.get("veiculo_id", "")
    motorista_id = request.args.get("motorista_id", "")
    combustivel = request.args.get("combustivel", "")
    min_litros = request.args.get("min_litros", "")
    max_litros = request.args.get("max_litros", "")
    setor_filtro = request.args.get("setor", "")

    query = Abastecimento.query.join(Veiculo).join(Motorista)
    if usuario_tipo == "admin" and setor_filtro:
        query = query.filter(Veiculo.tipo == setor_filtro)
    elif usuario_tipo != "admin" and usuario_setor:
        query = query.filter(Veiculo.tipo == usuario_setor)
    if data_inicio:
        try:
            data_inicio_obj = datetime.fromisoformat(data_inicio)
            query = query.filter(Abastecimento.data >= data_inicio_obj)
        except Exception:
            pass
    if data_fim:
        try:
            data_fim_obj = datetime.fromisoformat(data_fim)
            data_fim_obj = data_fim_obj.replace(hour=23, minute=59, second=59)
            query = query.filter(Abastecimento.data <= data_fim_obj)
        except Exception:
            pass
    if veiculo_id:
        try:
            query = query.filter(Abastecimento.veiculo_id == int(veiculo_id))
        except Exception:
            pass
    if motorista_id:
        try:
            query = query.filter(Abastecimento.motorista_id == int(motorista_id))
        except Exception:
            pass
    if combustivel:
        query = query.filter(Veiculo.combustivel == combustivel)
    if min_litros:
        try:
            query = query.filter(Abastecimento.litros >= float(min_litros))
        except Exception:
            pass
    if max_litros:
        try:
            query = query.filter(Abastecimento.litros <= float(max_litros))
        except Exception:
            pass

    abastecimentos = query.order_by(Abastecimento.data.desc()).all()
    total_litros = sum(a.litros for a in abastecimentos) if abastecimentos else 0
    valor_total = sum(a.valor_total for a in abastecimentos) if abastecimentos else 0
    media_litros = total_litros / len(abastecimentos) if abastecimentos else 0

    veiculos = Veiculo.query.order_by(Veiculo.placa).all()
    motoristas = Motorista.query.order_by(Motorista.nome_completo).all()
    setores = []
    if usuario_tipo == "admin":
        setores = [s[0] for s in db.session.query(User.setor).filter(User.setor != None).distinct().order_by(User.setor).all() if s[0]]

    filtros_aplicados = {}
    if usuario_tipo == "admin" and setor_filtro:
        filtros_aplicados["setor"] = setor_filtro
    if data_inicio:
        try:
            filtros_aplicados["data_inicio"] = datetime.fromisoformat(data_inicio).strftime('%d/%m/%Y')
        except Exception:
            filtros_aplicados["data_inicio"] = data_inicio
    if data_fim:
        try:
            filtros_aplicados["data_fim"] = datetime.fromisoformat(data_fim).strftime('%d/%m/%Y')
        except Exception:
            filtros_aplicados["data_fim"] = data_fim
    if veiculo_id:
        veiculo = Veiculo.query.get(veiculo_id)
        filtros_aplicados["veiculo_id"] = veiculo.placa if veiculo else veiculo_id
    if motorista_id:
        motorista = Motorista.query.get(motorista_id)
        filtros_aplicados["motorista_id"] = motorista.nome_completo if motorista else motorista_id
    if combustivel:
        filtros_aplicados["combustivel"] = combustivel
    if min_litros:
        filtros_aplicados["min_litros"] = min_litros
    if max_litros:
        filtros_aplicados["max_litros"] = max_litros

    agora = datetime.now()
    html = render_template(
        "relatorio_abastecimentos_print.html",
        abastecimentos=abastecimentos,
        veiculos=veiculos,
        motoristas=motoristas,
        tipos_combustivel=TIPOS_COMBUSTIVEL,
        filtros_aplicados=filtros_aplicados,
        total_registros=len(abastecimentos),
        setores=setores,
        agora=agora,
        total_litros=total_litros,
        valor_total=valor_total,
        media_litros=media_litros
    )
    pdf = HTML(string=html).write_pdf()
    return Response(pdf, mimetype='application/pdf', headers={"Content-Disposition": "attachment;filename=relatorio_abastecimentos.pdf"})

# ----------------------
# FUNÇÃO AUXILIAR: Coleta de Dados do Relatório
# ----------------------
def get_veiculos_report_data(request_args):
    """
    Função para coletar e estruturar os dados do Relatório por Veículo 
    baseada nos filtros da requisição (como data_inicio, data_fim, etc.).
    """
    # Exemplo simples de coleta de filtros, ajuste conforme a sua lógica completa
    data_inicio = request_args.get("data_inicio")
    data_fim = request_args.get("data_fim")
    
    query_abastecimentos = Abastecimento.query.join(Veiculo).join(Motorista)
    
    if data_inicio:
        try:
            data_inicio_obj = datetime.fromisoformat(data_inicio)
            query_abastecimentos = query_abastecimentos.filter(Abastecimento.data >= data_inicio_obj)
        except:
            pass
    
    if data_fim:
        try:
            data_fim_obj = datetime.fromisoformat(data_fim)
            data_fim_obj = data_fim_obj.replace(hour=23, minute=59, second=59)
            query_abastecimentos = query_abastecimentos.filter(Abastecimento.data <= data_fim_obj)
        except:
            pass

    # A lógica de Agrupamento por Veículo (para a estrutura do template)
    all_abastecimentos = query_abastecimentos.order_by(Veiculo.placa, Abastecimento.data.desc()).all()
    
    dados_por_veiculo = defaultdict(lambda: {'metadados': None, 'abastecimentos': [], 'total_litros': 0.0, 'total_valor': 0.0})
    
    for a in all_abastecimentos:
        placa = a.veiculo.placa
        if not dados_por_veiculo[placa]['metadados']:
            dados_por_veiculo[placa]['metadados'] = a.veiculo
        dados_por_veiculo[placa]['abastecimentos'].append(a)
        dados_por_veiculo[placa]['total_litros'] += a.litros
        dados_por_veiculo[placa]['total_valor'] += a.valor_total

    # Preparar filtros aplicados para o cabeçalho
    filtros_aplicados = {}
    if data_inicio:
        filtros_aplicados["data_inicio"] = data_inicio
    if data_fim:
        filtros_aplicados["data_fim"] = data_fim
    
    agora = datetime.now()
    
    return {
        'dados_por_veiculo': list(dados_por_veiculo.values()), # Converte para lista de dicionários
        'filtros_aplicados': filtros_aplicados,
        'agora': agora
    }
   
# Execução
# ----------------------
if __name__ == "__main__":
    app.run(debug=True, port=5000, host='0.0.0.0')
