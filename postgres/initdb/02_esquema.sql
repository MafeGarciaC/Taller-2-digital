-- Esquema de PepiCloud (prototipo). Los archivos NO se guardan aquí (sin BLOB/bytea):
-- solo los datos de la persona, el tipo, los metadatos y las referencias a las copias.

CREATE TABLE personas (
    id                     SERIAL PRIMARY KEY,
    tipo_identificacion    VARCHAR(5)   NOT NULL DEFAULT 'CC',
    numero_identificacion  VARCHAR(20)  NOT NULL,
    nombres                VARCHAR(100) NOT NULL,
    apellidos              VARCHAR(100) NOT NULL,
    fecha_nacimiento       DATE,
    sexo                   VARCHAR(1)   CHECK (sexo IN ('F', 'M', 'O')),
    telefono               VARCHAR(30),
    correo                 VARCHAR(120),
    creado_en              TIMESTAMPTZ  NOT NULL DEFAULT now(),
    actualizado_en         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (tipo_identificacion, numero_identificacion)
);

CREATE TABLE tipos_documento (
    id           SERIAL PRIMARY KEY,
    codigo       VARCHAR(30)  NOT NULL UNIQUE,   -- se usa en la clave del objeto
    nombre       VARCHAR(100) NOT NULL,
    descripcion  TEXT
);

INSERT INTO tipos_documento (codigo, nombre, descripcion) VALUES
  ('hemograma',          'Examen de sangre (hemograma)', 'Cuadro hemático: glóbulos rojos, blancos, plaquetas y hemoglobina'),
  ('laboratorio-clinico', 'Examen de laboratorio clínico', 'Orden, requisitos o resultado de exámenes de laboratorio'),
  ('uroanalisis',        'Examen de orina (uroanálisis)', 'Parcial de orina: físico-químico y sedimento'),
  ('radiografia',        'Radiografía',                   'Imagen diagnóstica por rayos X con su lectura'),
  ('formula-medica',     'Fórmula médica',                'Prescripción de medicamentos'),
  ('electrocardiograma', 'Electrocardiograma',            'Registro de la actividad eléctrica del corazón'),
  ('historia-clinica',   'Resumen de historia clínica',   'Epicrisis o resumen de atención');

CREATE TABLE documentos (
    id                   UUID PRIMARY KEY,
    persona_id           INTEGER      NOT NULL REFERENCES personas(id),
    tipo_documento_id    INTEGER      NOT NULL REFERENCES tipos_documento(id),
    titulo               VARCHAR(200) NOT NULL,
    descripcion          TEXT,
    fecha_documento      DATE,
    nombre_archivo       VARCHAR(255) NOT NULL,
    tipo_contenido       VARCHAR(100) NOT NULL,
    tamano_bytes         BIGINT       NOT NULL,
    sha256               CHAR(64)     NOT NULL,      -- huella del archivo original para verificar cada copia
    clave_objeto         VARCHAR(400) NOT NULL,      -- referencia para localizar el archivo en el almacenamiento
    algoritmo_cifrado    VARCHAR(30)  NOT NULL DEFAULT 'AES-256-GCM',
    clave_datos_cifrada  TEXT         NOT NULL,      -- clave del documento, cifrada con la clave maestra
    retencion_hasta      TIMESTAMPTZ  NOT NULL,      -- inmutable hasta esta fecha (Object Lock)
    creado_en            TIMESTAMPTZ  NOT NULL DEFAULT now(),
    eliminado_en         TIMESTAMPTZ
);
CREATE INDEX ix_documentos_persona ON documentos (persona_id);

-- Las 2 copias operativas y el backup documental de cada documento
CREATE TABLE copias_documento (
    id              SERIAL PRIMARY KEY,
    documento_id    UUID         NOT NULL REFERENCES documentos(id),
    ubicacion       VARCHAR(20)  NOT NULL CHECK (ubicacion IN ('nodo-a', 'nodo-b', 'respaldo')),
    rol             VARCHAR(20)  NOT NULL CHECK (rol IN ('operativa', 'respaldo')),
    bucket          VARCHAR(100) NOT NULL,
    clave_objeto    VARCHAR(400) NOT NULL,
    version_id      VARCHAR(200),
    estado          VARCHAR(20)  NOT NULL CHECK (estado IN ('ok', 'pendiente', 'perdida', 'eliminada')),
    actualizado_en  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (documento_id, ubicacion)
);

-- Bitácora de lo que hace el sistema (evidencia para la demostración)
CREATE TABLE eventos (
    id            BIGSERIAL PRIMARY KEY,
    fecha         TIMESTAMPTZ NOT NULL DEFAULT now(),
    tipo          VARCHAR(40) NOT NULL,
    documento_id  UUID,
    detalle       TEXT        NOT NULL
);

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO pepicloud_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO pepicloud_app;
