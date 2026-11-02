-- Esquema PostgreSQL de MXL Lab. Es idempotente: se ejecuta en cada arranque.

CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    username      VARCHAR(32) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    points        INTEGER NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lessons (
    id           SERIAL PRIMARY KEY,
    slug         VARCHAR(64) UNIQUE NOT NULL,
    title        TEXT NOT NULL,
    content      TEXT NOT NULL,
    starter_code TEXT NOT NULL DEFAULT '',
    position     INTEGER NOT NULL DEFAULT 0
);

-- Historial de lecciones: una fila por cada ejecución hecha dentro de una lección.
CREATE TABLE IF NOT EXISTS lesson_history (
    id         BIGSERIAL PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    lesson_id  INTEGER NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    success    BOOLEAN NOT NULL,
    run_ms     INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_lesson_history_user ON lesson_history (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS challenges (
    id              SERIAL PRIMARY KEY,
    slug            VARCHAR(64) UNIQUE NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL,
    starter_code    TEXT NOT NULL DEFAULT '',
    test_input      TEXT NOT NULL DEFAULT '',
    expected_output TEXT NOT NULL,
    points          INTEGER NOT NULL DEFAULT 10
);

-- Retos completados (uno por usuario y reto).
CREATE TABLE IF NOT EXISTS challenge_completions (
    id           BIGSERIAL PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    challenge_id INTEGER NOT NULL REFERENCES challenges(id) ON DELETE CASCADE,
    attempts     INTEGER NOT NULL DEFAULT 1,
    completed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, challenge_id)
);

-- Calificaciones del auditor; el informe detallado vive en MongoDB.
CREATE TABLE IF NOT EXISTS auditor_grades (
    id              BIGSERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    lesson_id       INTEGER REFERENCES lessons(id) ON DELETE SET NULL,
    challenge_id    INTEGER REFERENCES challenges(id) ON DELETE SET NULL,
    score           INTEGER NOT NULL CHECK (score BETWEEN 0 AND 100),
    grade           CHAR(1) NOT NULL,
    mongo_report_id VARCHAR(24),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_grades_user ON auditor_grades (user_id, created_at DESC);

-- Contenido inicial
INSERT INTO lessons (slug, title, content, starter_code, position) VALUES
('variables', 'Variables y tipos',
 $$Una variable guarda un valor con un nombre. Python infiere el tipo: int, float, str, bool. Crea dos variables e imprime su suma.$$,
 $$edad = 30
altura = 1.75
print(edad + altura)
print(type(edad), type(altura))
$$, 1),
('condicionales', 'Condicionales',
 $$if, elif y else eligen qué bloque se ejecuta según una condición. Completa la función para clasificar un número.$$,
 $$def clasificar(n):
    if n < 0:
        return "negativo"
    elif n == 0:
        return "cero"
    return "positivo"


print(clasificar(5))
$$, 2),
('bucles', 'Bucles for y while',
 $$for recorre una secuencia y while repite mientras una condición sea verdadera. Imprime la tabla del 7.$$,
 $$for i in range(1, 11):
    print(f"7 x {i} = {7 * i}")
$$, 3)
ON CONFLICT (slug) DO NOTHING;

INSERT INTO challenges (slug, title, description, starter_code, test_input, expected_output, points) VALUES
('suma-dos', 'Suma de dos números',
 $$Lee dos enteros (uno por línea) con input() e imprime su suma.$$,
 $$a = int(input())
b = int(input())
# imprime la suma
$$, E'3\n4\n', '7', 10),
('fizzbuzz', 'FizzBuzz hasta n',
 $$Lee n e imprime del 1 a n: "Fizz" si es múltiplo de 3, "Buzz" si lo es de 5, "FizzBuzz" si de ambos.$$,
 $$n = int(input())
# tu solución
$$, E'15\n', E'1\n2\nFizz\n4\nBuzz\nFizz\n7\n8\nFizz\nBuzz\n11\nFizz\n13\n14\nFizzBuzz', 20),
('palindromo', 'Palíndromo',
 $$Lee una palabra e imprime True si se lee igual al revés, False si no.$$,
 $$palabra = input().strip().lower()
# tu solución
$$, E'reconocer\n', 'True', 15)
ON CONFLICT (slug) DO NOTHING;
