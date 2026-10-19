-- Nirog MySQL 8 schema, generated from the SQLAlchemy models.
-- Regenerate with: python -c "..." (see docs/SETUP.md)
SET NAMES utf8mb4;

CREATE TABLE audit_log (
	id BIGINT NOT NULL AUTO_INCREMENT, 
	correlation_id VARCHAR(36) NOT NULL, 
	actor_type VARCHAR(20) NOT NULL, 
	actor_id INTEGER, 
	event VARCHAR(60) NOT NULL, 
	subject_type VARCHAR(40), 
	subject_id VARCHAR(64), 
	detail TEXT, 
	ip_hash VARCHAR(64), 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE lab_tests (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	code VARCHAR(40) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	parameter_count INTEGER NOT NULL, 
	sample_type VARCHAR(60), 
	fasting_required BOOL NOT NULL, 
	turnaround_hours INTEGER, 
	price_paise INTEGER NOT NULL, 
	mrp_paise INTEGER NOT NULL, 
	is_package BOOL NOT NULL, 
	audience VARCHAR(120), 
	tag VARCHAR(60), 
	nabl_lab_name VARCHAR(160), 
	is_active BOOL NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE manufacturers (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	name VARCHAR(200) NOT NULL, 
	slug VARCHAR(200) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE molecules (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	canonical_name VARCHAR(160) NOT NULL, 
	slug VARCHAR(160) NOT NULL, 
	atc_code VARCHAR(16), 
	is_banned BOOL NOT NULL, 
	banned_reference VARCHAR(255), 
	PRIMARY KEY (id), 
	UNIQUE (canonical_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE pharmacies (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	name VARCHAR(200) NOT NULL, 
	drug_licence_number VARCHAR(80) NOT NULL, 
	licence_valid_till DATE, 
	registered_pharmacist_name VARCHAR(160) NOT NULL, 
	pharmacist_registration_number VARCHAR(80) NOT NULL, 
	state VARCHAR(80), 
	pincode VARCHAR(10), 
	is_active BOOL NOT NULL, 
	PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE specialties (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	code VARCHAR(30) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	icon VARCHAR(40), 
	accent VARCHAR(20), 
	PRIMARY KEY (id), 
	UNIQUE (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE users (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	mobile VARCHAR(15) NOT NULL, 
	email VARCHAR(200), 
	full_name VARCHAR(160) NOT NULL, 
	password_hash VARCHAR(255), 
	abha_number VARCHAR(20), 
	abha_linked_at DATETIME, 
	is_active BOOL NOT NULL, 
	is_verified BOOL NOT NULL, 
	created_at DATETIME NOT NULL, 
	last_login_at DATETIME, 
	PRIMARY KEY (id), 
	UNIQUE (email), 
	UNIQUE (abha_number)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE addresses (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	user_id INTEGER NOT NULL, 
	label VARCHAR(40), 
	line1 VARCHAR(255) NOT NULL, 
	line2 VARCHAR(255), 
	city VARCHAR(80) NOT NULL, 
	state VARCHAR(80) NOT NULL, 
	pincode VARCHAR(10) NOT NULL, 
	is_default BOOL NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE consents (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	user_id INTEGER NOT NULL, 
	purpose VARCHAR(80) NOT NULL, 
	granted BOOL NOT NULL, 
	granted_at DATETIME NOT NULL, 
	withdrawn_at DATETIME, 
	policy_version VARCHAR(20) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE doctors (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	full_name VARCHAR(160) NOT NULL, 
	specialty_id INTEGER NOT NULL, 
	qualification VARCHAR(200) NOT NULL, 
	nmc_registration_number VARCHAR(80) NOT NULL, 
	registration_council VARCHAR(120), 
	registration_verified_at DATETIME, 
	experience_years INTEGER, 
	languages VARCHAR(240), 
	affiliation VARCHAR(200), 
	about TEXT, 
	consultation_fee_paise INTEGER NOT NULL, 
	rating NUMERIC(2, 1), 
	review_count INTEGER, 
	is_online BOOL NOT NULL, 
	is_active BOOL NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(specialty_id) REFERENCES specialties (id), 
	UNIQUE (nmc_registration_number)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE emergency_contacts (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	user_id INTEGER NOT NULL, 
	name VARCHAR(160) NOT NULL, 
	relation VARCHAR(60), 
	mobile VARCHAR(15) NOT NULL, 
	priority INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE family_members (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	user_id INTEGER NOT NULL, 
	full_name VARCHAR(160) NOT NULL, 
	relation VARCHAR(40) NOT NULL, 
	date_of_birth DATE, 
	sex VARCHAR(20), 
	is_self BOOL NOT NULL, 
	is_minor BOOL NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE molecule_aliases (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	molecule_id INTEGER NOT NULL, 
	alias VARCHAR(160) NOT NULL, 
	normalised VARCHAR(160) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_alias_per_molecule UNIQUE (molecule_id, normalised), 
	FOREIGN KEY(molecule_id) REFERENCES molecules (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE products (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	sku VARCHAR(64) NOT NULL, 
	brand_name VARCHAR(240) NOT NULL, 
	composition_text VARCHAR(500) NOT NULL, 
	composition_key VARCHAR(500) NOT NULL, 
	molecule_key VARCHAR(400) NOT NULL, 
	dosage_form VARCHAR(60) NOT NULL, 
	form_family VARCHAR(40) NOT NULL, 
	pack_label VARCHAR(120) NOT NULL, 
	pack_units INTEGER, 
	manufacturer_id INTEGER, 
	mrp_paise INTEGER NOT NULL, 
	selling_price_paise INTEGER NOT NULL, 
	drug_schedule VARCHAR(8) NOT NULL, 
	is_prescription_required BOOL NOT NULL, 
	is_nlem BOOL NOT NULL, 
	nppa_ceiling_price_paise INTEGER, 
	hsn_code VARCHAR(12), 
	category VARCHAR(80), 
	is_discontinued BOOL NOT NULL, 
	is_active BOOL NOT NULL, 
	rating NUMERIC(2, 1), 
	uses TEXT, 
	mechanism TEXT, 
	side_effects TEXT, 
	directions TEXT, 
	storage TEXT, 
	content_source VARCHAR(120), 
	content_reviewed_by VARCHAR(160), 
	content_reviewed_at DATETIME, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(manufacturer_id) REFERENCES manufacturers (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE carts (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	user_id INTEGER NOT NULL, 
	member_id INTEGER, 
	is_open BOOL NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	FOREIGN KEY(member_id) REFERENCES family_members (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE doctor_slots (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	doctor_id INTEGER NOT NULL, 
	starts_at DATETIME NOT NULL, 
	duration_minutes INTEGER NOT NULL, 
	is_booked BOOL NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(doctor_id) REFERENCES doctors (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE health_records (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	user_id INTEGER NOT NULL, 
	member_id INTEGER NOT NULL, 
	record_type VARCHAR(30) NOT NULL, 
	title VARCHAR(240) NOT NULL, 
	issued_by VARCHAR(200), 
	record_date DATE NOT NULL, 
	file_key VARCHAR(500), 
	file_mime VARCHAR(80), 
	source VARCHAR(40) NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	FOREIGN KEY(member_id) REFERENCES family_members (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE inventory (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	product_id INTEGER NOT NULL, 
	pharmacy_id INTEGER NOT NULL, 
	batch_number VARCHAR(64), 
	expiry_date DATE, 
	quantity_available INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(product_id) REFERENCES products (id), 
	FOREIGN KEY(pharmacy_id) REFERENCES pharmacies (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE lab_bookings (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	reference VARCHAR(24) NOT NULL, 
	user_id INTEGER NOT NULL, 
	member_id INTEGER, 
	lab_test_id INTEGER NOT NULL, 
	address_id INTEGER, 
	scheduled_for DATETIME, 
	status VARCHAR(30) NOT NULL, 
	phlebotomist_name VARCHAR(160), 
	amount_paise INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (reference), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	FOREIGN KEY(member_id) REFERENCES family_members (id), 
	FOREIGN KEY(lab_test_id) REFERENCES lab_tests (id), 
	FOREIGN KEY(address_id) REFERENCES addresses (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE medication_reminders (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	member_id INTEGER NOT NULL, 
	product_id INTEGER, 
	label VARCHAR(200) NOT NULL, 
	dose_instruction VARCHAR(200), 
	time_of_day VARCHAR(10) NOT NULL, 
	is_active BOOL NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(member_id) REFERENCES family_members (id), 
	FOREIGN KEY(product_id) REFERENCES products (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE prescriptions (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	user_id INTEGER NOT NULL, 
	member_id INTEGER, 
	file_path VARCHAR(500) NOT NULL, 
	file_hash VARCHAR(64) NOT NULL, 
	uploaded_at DATETIME NOT NULL, 
	doctor_name VARCHAR(160), 
	doctor_registration_number VARCHAR(80), 
	prescribed_on DATE, 
	valid_until DATE, 
	verification_status VARCHAR(20) NOT NULL, 
	verified_by_pharmacist VARCHAR(160), 
	pharmacist_registration_number VARCHAR(80), 
	verified_at DATETIME, 
	rejection_reason VARCHAR(255), 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	FOREIGN KEY(member_id) REFERENCES family_members (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE product_ingredients (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	product_id INTEGER NOT NULL, 
	molecule_id INTEGER NOT NULL, 
	strength_value NUMERIC(12, 4), 
	strength_unit VARCHAR(16), 
	strength_base_value NUMERIC(16, 6), 
	strength_base_unit VARCHAR(16), 
	position INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(product_id) REFERENCES products (id), 
	FOREIGN KEY(molecule_id) REFERENCES molecules (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE vital_readings (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	member_id INTEGER NOT NULL, 
	metric VARCHAR(40) NOT NULL, 
	value NUMERIC(10, 3) NOT NULL, 
	unit VARCHAR(20) NOT NULL, 
	measured_on DATE NOT NULL, 
	source VARCHAR(40) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(member_id) REFERENCES family_members (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE cart_items (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	cart_id INTEGER NOT NULL, 
	product_id INTEGER NOT NULL, 
	quantity INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_cart_product UNIQUE (cart_id, product_id), 
	FOREIGN KEY(cart_id) REFERENCES carts (id), 
	FOREIGN KEY(product_id) REFERENCES products (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE consultations (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	reference VARCHAR(24) NOT NULL, 
	user_id INTEGER NOT NULL, 
	member_id INTEGER, 
	doctor_id INTEGER NOT NULL, 
	slot_id INTEGER, 
	mode VARCHAR(10) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	chief_complaint TEXT, 
	fee_paise INTEGER NOT NULL, 
	started_at DATETIME, 
	ended_at DATETIME, 
	follow_up_until DATE, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (reference), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	FOREIGN KEY(member_id) REFERENCES family_members (id), 
	FOREIGN KEY(doctor_id) REFERENCES doctors (id), 
	FOREIGN KEY(slot_id) REFERENCES doctor_slots (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE orders (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	order_number VARCHAR(24) NOT NULL, 
	idempotency_key VARCHAR(64), 
	user_id INTEGER NOT NULL, 
	member_id INTEGER, 
	address_id INTEGER, 
	pharmacy_id INTEGER, 
	prescription_id INTEGER, 
	status VARCHAR(30) NOT NULL, 
	requires_prescription BOOL NOT NULL, 
	subtotal_paise INTEGER NOT NULL, 
	discount_paise INTEGER NOT NULL, 
	delivery_paise INTEGER NOT NULL, 
	total_paise INTEGER NOT NULL, 
	payment_status VARCHAR(20) NOT NULL, 
	payment_reference VARCHAR(80), 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	FOREIGN KEY(member_id) REFERENCES family_members (id), 
	FOREIGN KEY(address_id) REFERENCES addresses (id), 
	FOREIGN KEY(pharmacy_id) REFERENCES pharmacies (id), 
	FOREIGN KEY(prescription_id) REFERENCES prescriptions (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE reminder_logs (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	reminder_id INTEGER NOT NULL, 
	due_on DATE NOT NULL, 
	taken_at DATETIME, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_reminder_day UNIQUE (reminder_id, due_on), 
	FOREIGN KEY(reminder_id) REFERENCES medication_reminders (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE order_events (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	order_id INTEGER NOT NULL, 
	status VARCHAR(30) NOT NULL, 
	actor VARCHAR(80) NOT NULL, 
	note VARCHAR(500), 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(order_id) REFERENCES orders (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE order_items (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	order_id INTEGER NOT NULL, 
	product_id INTEGER NOT NULL, 
	brand_name_snapshot VARCHAR(240) NOT NULL, 
	composition_snapshot VARCHAR(500) NOT NULL, 
	pack_snapshot VARCHAR(120) NOT NULL, 
	drug_schedule_snapshot VARCHAR(8) NOT NULL, 
	quantity INTEGER NOT NULL, 
	unit_price_paise INTEGER NOT NULL, 
	unit_mrp_paise INTEGER NOT NULL, 
	line_total_paise INTEGER NOT NULL, 
	batch_number VARCHAR(64), 
	expiry_date DATE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(order_id) REFERENCES orders (id), 
	FOREIGN KEY(product_id) REFERENCES products (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
