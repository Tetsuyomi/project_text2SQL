from flask import Flask, request, render_template, jsonify
import json
import sqlite3
from openai import OpenAI
import httpx

app = Flask(__name__, template_folder='templates', static_folder='static')

# Initialize LLM7 client
client = OpenAI(
    base_url="https://api.llm7.io/v1",
    api_key="unused",
    http_client=httpx.Client()  # Явно задаем HTTP-клиент без прокси
)

# Store schema globally (for simplicity)
db_schema = {}

def generate_sql_query(natural_language_query, schema):
    schema_str = json.dumps(schema, indent=2)
    prompt = f"""
    Ты эксперт по генерации SQL-запросов. На основе схемы базы данных и запроса на естественном языке создайте корректный SQL-запрос для PostgreSQL.

    Схема базы данных:
    {schema_str}

    Запрос на естественном языке:
    {natural_language_query}

    Предоставьте только SQL-запрос в виде обычного текста. Убедитесь, что он корректен для PostgreSQL.
    Не позволяйте пользователю выполнять диструктивные действия.
    
    В ответе должен быть только SQL-запрос.
    """
    response = client.chat.completions.create(
        model="gpt-4.1-nano",
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content.strip()

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        try:
            # Handle schema upload
            if "schema_file" in request.files:
                schema_file = request.files["schema_file"]
                if schema_file.filename.endswith(".json"):
                    global db_schema
                    db_schema = json.load(schema_file)
                    return jsonify({"status": "Schema uploaded successfully"})
                else:
                    return jsonify({"error": "Please upload a JSON file"}), 400

            # Handle text-to-SQL query
            if "query" in request.form:
                natural_language_query = request.form["query"]
                if not db_schema:
                    return jsonify({"error": "No schema uploaded"}), 400
                sql_query = generate_sql_query(natural_language_query, db_schema)
                return jsonify({"sql_query": sql_query})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    return render_template("index.html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)