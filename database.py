from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class User(db.Model):
    __tablename__ = 'user'

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    senha_hash = db.Column(db.String(128), nullable=False)
    tipo = db.Column(db.String(20), nullable=False)  # 'admin' ou 'departamento'
    setor = db.Column(db.String(100), nullable=True)  # Null para admin, obrigatório para usuário de departamento
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f'<User {self.email} - {self.tipo}>'


class Veiculo(db.Model):
    __tablename__ = 'veiculo'

    id = db.Column(db.Integer, primary_key=True)
    placa = db.Column(db.String(10), unique=True, nullable=False)
    tipo = db.Column(db.String(20), nullable=False)
    combustivel = db.Column(db.String(50), nullable=False)
    capacidade_tanque = db.Column(db.Float, nullable=True)

    def __repr__(self):
        return f'<Veiculo {self.placa} - {self.tipo}>'


class Motorista(db.Model):
    __tablename__ = 'motorista'

    id = db.Column(db.Integer, primary_key=True)
    nome_completo = db.Column(db.String(100), nullable=False)
    documento = db.Column(db.String(50), nullable=False, unique=True)
    observacoes = db.Column(db.Text, nullable=True)
    setor = db.Column(db.String(100), nullable=True)  # Setor/departamento do motorista

    def __repr__(self):
        return f'<Motorista {self.nome_completo}>'


class ContratoCombustivel(db.Model):
    __tablename__ = 'contrato_combustivel'

    id = db.Column(db.Integer, primary_key=True)
    numero_contrato = db.Column(db.String(50), nullable=False)
    ano_contrato = db.Column(db.Integer, nullable=False)
    data_inicio_contrato = db.Column(db.Date, nullable=False)
    data_fim_contrato = db.Column(db.Date, nullable=False)
    fornecedor = db.Column(db.String(100), nullable=False)
    observacoes = db.Column(db.Text, nullable=True)
    setor = db.Column(db.String(100), nullable=True)  # Setor/departamento do contrato
    ativo = db.Column(db.Boolean, default=True, nullable=False)
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # Relacionamento com os itens
    itens = db.relationship('ContratoCombustivelItem', back_populates='contrato', cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Contrato {self.numero_contrato}/{self.ano_contrato} - {self.fornecedor}>'

    @property
    def valor_total(self):
        return sum(item.valor_total for item in self.itens)

    @property
    def quantidade_total(self):
        return sum(item.quantidade for item in self.itens)

    @property
    def valor_por_litro_medio(self):
        total_valor = self.valor_total
        total_quantidade = self.quantidade_total
        if total_quantidade > 0:
            return total_valor / total_quantidade
        return 0.0


class ContratoCombustivelItem(db.Model):
    __tablename__ = 'contrato_combustivel_item'

    id = db.Column(db.Integer, primary_key=True)
    contrato_id = db.Column(db.Integer, db.ForeignKey('contrato_combustivel.id'), nullable=False)
    tipo_combustivel = db.Column(db.String(50), nullable=False)
    quantidade = db.Column(db.Float, nullable=False)  # em litros
    valor_total = db.Column(db.Float, nullable=False)  # valor total do item
    valor_por_litro = db.Column(db.Float, nullable=False)

    # Relacionamento com o contrato
    contrato = db.relationship('ContratoCombustivel', back_populates='itens')

    def __repr__(self):
        return f'<Item {self.tipo_combustivel} - {self.quantidade}L>'


# Modelagem de aditivos de contrato de combustível
class AditivoContratoCombustivel(db.Model):
    __tablename__ = 'aditivo_contrato_combustivel'

    id = db.Column(db.Integer, primary_key=True)
    contrato_id = db.Column(db.Integer, db.ForeignKey('contrato_combustivel.id'), nullable=False)
    tipo_aditivo = db.Column(db.String(50), nullable=False)  # Ex: Prorrogação, Reajuste, Aumento, Outro
    descricao = db.Column(db.Text, nullable=True)
    data_aditivo = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    novo_valor_total = db.Column(db.Float, nullable=True)
    nova_quantidade_total = db.Column(db.Float, nullable=True)
    nova_data_fim = db.Column(db.Date, nullable=True)

    contrato = db.relationship('ContratoCombustivel', backref='aditivos')

    def __repr__(self):
        return f'<Aditivo {self.tipo_aditivo} para Contrato {self.contrato_id}>'


class Abastecimento(db.Model):
    __tablename__ = 'abastecimento'

    id = db.Column(db.Integer, primary_key=True)
    data = db.Column(db.DateTime, nullable=False)
    veiculo_id = db.Column(db.Integer, db.ForeignKey('veiculo.id'), nullable=False)
    motorista_id = db.Column(db.Integer, db.ForeignKey('motorista.id'), nullable=False)
    hodometro = db.Column(db.Integer, nullable=False)
    litros = db.Column(db.Float, nullable=False)
    valor_total = db.Column(db.Float, nullable=False)
    numero_nota = db.Column(db.String(50), nullable=False)
    observacoes = db.Column(db.Text, nullable=True)
    combustivel = db.Column(db.String(50), nullable=True)
    contrato_id = db.Column(db.Integer, db.ForeignKey('contrato_combustivel.id'), nullable=True)

    # Relacionamentos
    veiculo = db.relationship('Veiculo', backref='abastecimentos')
    motorista = db.relationship('Motorista', backref='abastecimentos')
    contrato = db.relationship('ContratoCombustivel', backref='abastecimentos_vinculados')

    def __repr__(self):
        return f'<Abastecimento {self.id} - {self.litros}L em {self.data}>'
