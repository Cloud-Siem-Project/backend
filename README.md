# 🚀 Cloud SIEM Platform Backend

Bu proje, **Cloud Security Information and Event Management (SIEM)** sisteminin prototipini geliştirmeyi amaçlayan bir backend platformudur.

Sistem farklı makinelerden gelen logları toplar, bu logları analiz eder, şüpheli aktiviteleri tespit eder ve güvenlik ekiplerinin inceleyebilmesi için **alert** ve **incident** kayıtları oluşturur.

---

## 🎯 Projenin Amacı

Bu platformun amacı aşağıdaki güvenlik izleme süreçlerini gerçekleştirebilen bir sistem geliştirmektir:

* Dağıtık makinelerden log toplamak
* Farklı log formatlarını standart hale getirmek
* Güvenlik olaylarını analiz etmek
* Şüpheli aktiviteleri tespit etmek
* Alert üretmek
* Incident yönetimi sağlamak
* Güvenlik dashboardları için veri sunmak

---

## 🏗️ System Architecture

Bu platform **distributed architecture** kullanır:

```text
+-------------------+
|   Worker Agent    |
| (Log Collector)   |
+-------------------+
          │
          │ Logs / Heartbeat
          ▼
+-------------------+
|    Master Node    |
| Node Management   |
+-------------------+
          │
          ▼
+-------------------+
|    Backend API    |
|  Event Pipeline   |
+-------------------+
          │
          ▼
+-------------------+
| Detection Engine  |
+-------------------+
          │
          ▼
+-------------------+
|   Alert System    |
+-------------------+
          │
          ▼
+-------------------+
| Incident Manager  |
+-------------------+
          │
          ▼
+-------------------+
|   Dashboard API   |
+-------------------+
```

---

## ⚙️ Ana Sistem Bileşenleri

### 🤖 Worker Agent

İzlenen makinelerde çalışan bir programdır.

**Görevleri:**

* Sistem bilgilerini toplamak
* Açık portları tespit etmek
* Logları toplamak
* Master sunucuya heartbeat göndermek
* Logları merkezi sisteme iletmek
* Register işlemi yapmak

---

### 🧠 Master Server

Worker node'ları yönetir.

**Görevleri:**

* Worker kayıtlarını almak
* Node health durumunu izlemek
* Heartbeat kontrolü yapmak
* Cluster durumunu göstermek

---

### 🔥 Backend API

Sistemin merkezidir.

**Görevleri:**

* Worker'lardan gelen logları almak
* Raw logları saklamak
* Event üretmek
* Detection kurallarını çalıştırmak
* Alert üretmek
* Incident yönetmek
* Dashboard verisi sağlamak

Backend **FastAPI** kullanılarak geliştirilmektedir.

---

## 🔄 Event Processing Pipeline

### 📦 Raw Log Storage

Worker'lardan gelen loglar önce ham şekilde saklanır.

Bu sayede:

* yeniden işleme yapılabilir
* hata ayıklama kolaylaşır
* audit log tutulur

---

### 🔄 Normalization

Farklı log formatları tek bir standart event yapısına çevrilir.

**Örnek event alanları:**

* timestamp
* source
* event_type
* severity
* username
* ip_address
* action

---

### 🧹 Filtering

Gürültü oluşturan ve gereksiz loglar filtrelenir.

---

### ➕ Enrichment

Event'lere ek bağlam bilgisi eklenir.

**Örnek:**

* node bilgisi
* kullanıcı bilgisi
* kaynak türü

---

### 🧠 Detection Engine

Event'leri analiz eder ve şüpheli davranışları tespit eder.

---

### 🚨 Alert Sistemi

Şüpheli aktiviteler tespit edildiğinde sistem alert üretir.

**Alert bilgileri:**

* severity
* açıklama
* ilgili eventler
* kaynak node

**Alert lifecycle:**

```
Open → Acknowledged → Resolved
```

---

### 🧩 Incident Yönetimi

Alert'ler güvenlik ekipleri tarafından incident olarak ele alınabilir.

**Incident sistemi:**

* incident oluşturma
* incident atama
* inceleme notları
* durum güncelleme
* incident kapatma

---

## 📁 Proje Repo Yapısı

```bash
cloud-siem-project/
├── app/
│   ├── controllers/
│   ├── routes/
│   ├── models/
│   ├── services/
│   ├── schemas/
│   ├── helpers/
│   ├── config/
│   ├── db/
│   └── main.py
│
├── agents/
│   ├── master.py
│   └── worker.py
│
├── scripts/
│   ├── init_db.py
│   └── run_api.py
│
├── tests/
│
├── requirements.txt
├── .env.example
├── README.md
└── run.py
```

---

## 🧰 Kullanılan Teknolojiler

* **Backend:** Python, FastAPI
* **Database:** PostgreSQL
* **ORM:** SQLAlchemy
* **Architecture:** Distributed (Worker + Master)

---

## ⚙️ Kurulum ve Çalıştırma

### 1️⃣ PostgreSQL Kurulumu

#### Mac (Homebrew)

```bash
brew install postgresql
brew services start postgresql
```

#### Windows

👉 https://www.postgresql.org/download/windows/

Kurulum sırasında:

* şifre belirle
* port: 5432 bırak

#### Linux (Ubuntu)

```bash
sudo apt update
sudo apt install postgresql postgresql-contrib
sudo service postgresql start
```

---

### 2️⃣ Database oluştur

```bash
psql postgres
CREATE DATABASE cloudsiem;
\q
```

---

### 3️⃣ Kullanıcı ve şifre ayarla

```sql
ALTER USER YOUR_USERNAME PASSWORD 'your_password';
```

---

### 4️⃣ .env dosyası oluştur

```env
DATABASE_URL=postgresql+psycopg2://username:password@localhost:5432/cloudsiem
```

---

### 5️⃣ Bağımlılıkları yükle

```bash
pip install -r requirements.txt
```

---

### 6️⃣ Backend’i başlat

```bash
uvicorn app.main:app --reload
```

---

### 7️⃣ Swagger

👉 http://localhost:8000/docs

---

## 🤖 Worker Çalıştırma

```bash
python agents/worker.py --master http://127.0.0.1:9800
```

---

## 🧪 Bağlantı Testi

```bash
psql -U username -d cloudsiem -W
```

Bağlanabiliyorsanız kurulum başarılıdır ✅

