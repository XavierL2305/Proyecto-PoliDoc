FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Instalar dependencias del sistema + LibreOffice para conversión de documentos
RUN apt-get update && apt-get install -y \
    gcc \
    build-essential \
    libldap2-dev \
    libsasl2-dev \
    libssl-dev \
    nodejs \
    npm \
    libreoffice \
    libreoffice-writer \
    libreoffice-core \
    fonts-liberation \
    fonts-crosextra-carlito \
    && rm -rf /var/lib/apt/lists/*

# Ahora sí puedes instalar opencode globalmente
RUN npm i -g opencode-ai

RUN pip install --no-cache-dir django-auth-ldap

COPY requirements.txt /app/

RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/