CREATE TABLE utilisateurs (
  utilisateur_id serial PRIMARY KEY,
  username varchar(100),
  mot_de_passe_hash text NOT NULL,
  role varchar(20) CHECK (role IN ('Admin', 'Scientifique', 'Médecin')) NOT NULL
);

CREATE TABLE seminaristes (
  matricule varchar(20) PRIMARY KEY,
  prenom varchar(100) NOT NULL,
  nom varchar(100) NOT NULL,
  photo text,
  date_de_naissance date,
  genre varchar(10) CHECK (genre IN ('Homme', 'Femme')) NOT NULL,
  niveau varchar(50),
  contact varchar(20),
  email varchar(100),
  commune varchar(100) NOT NULL,
  statut varchar(50) NOT NULL,
  niveau_etude varchar(100),
  contact_parent varchar(20),
  allergies text,
  antecedents_medicaux text
);

CREATE TABLE notes (
  note_id serial PRIMARY KEY,
  matricule varchar(20) NOT NULL,
  nomnote varchar(100) NOT NULL,
  note numeric(5, 2) NOT NULL,
  observation text,
  date_enregistrement date DEFAULT CURRENT_DATE
);

CREATE TABLE consultations (
  consultation_id serial PRIMARY KEY,
  matricule varchar(20) NOT NULL,
  medecin_id integer NOT NULL,
  medecin_responsable varchar(100) NOT NULL,
  motif text NOT NULL,
  diagnostic text,
  notes_medicales text,
  prescription text,
  date_consultation date DEFAULT CURRENT_DATE
);

-- Définition des clés étrangères
ALTER TABLE notes ADD CONSTRAINT fk_notes_matricule FOREIGN KEY (matricule) REFERENCES seminaristes (matricule);
ALTER TABLE consultations ADD CONSTRAINT fk_consultations_matricule FOREIGN KEY (matricule) REFERENCES seminaristes (matricule);
ALTER TABLE consultations ADD CONSTRAINT fk_consultations_medecin FOREIGN KEY (medecin_id) REFERENCES utilisateurs (utilisateur_id);
