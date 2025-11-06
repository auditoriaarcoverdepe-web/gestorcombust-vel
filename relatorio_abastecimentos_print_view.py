from flask import render_template, request, session, redirect, url_for
from datetime import datetime
from app import app, db
from database import Abastecimento, Veiculo, Motorista, User, TIPOS_COMBUSTIVEL

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
