-- Bayan AI — PostgreSQL продакшны инвариант триггерүүд (G1-G3-ийг DB түвшинд давхарлана)
-- Сервисийн давхарга (ledger.py) мөн шалгадаг; энэ нь "хоёр дахь цоож".
-- Ажиллуулах: psql -d bayan -f migrations/001_pg_triggers.sql

-- G1: Бичилт бүрд Σдебит = Σкредит (transaction commit-ийн үед)
CREATE OR REPLACE FUNCTION check_entry_balanced() RETURNS trigger AS $$
DECLARE d BIGINT; c BIGINT;
BEGIN
  SELECT COALESCE(SUM(debit_minor),0), COALESCE(SUM(credit_minor),0)
    INTO d, c FROM journal_line WHERE entry_id = NEW.entry_id;
  IF d <> c OR d = 0 THEN
    RAISE EXCEPTION 'G1: entry % тэнцээгүй (Дт % ≠ Кт %)', NEW.entry_id, d, c;
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER trg_entry_balanced
  AFTER INSERT OR UPDATE ON journal_line
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION check_entry_balanced();

-- G2: Posted бичилт өөрчлөгдөхгүй, устгагдахгүй (status солихоос бусад UPDATE хориотой)
CREATE OR REPLACE FUNCTION block_posted_mutation() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'G2: журналын бичилт устгахгүй — reversal ашиглана';
  END IF;
  IF OLD.status = 'posted' AND (
       NEW.entry_date <> OLD.entry_date OR NEW.company_id <> OLD.company_id
       OR NEW.source_type <> OLD.source_type) THEN
    RAISE EXCEPTION 'G2: posted бичилт өөрчлөгдөхгүй (id=%)', OLD.id;
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_posted_immutable
  BEFORE UPDATE OR DELETE ON journal_entry
  FOR EACH ROW EXECUTE FUNCTION block_posted_mutation();

CREATE OR REPLACE FUNCTION block_line_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'G2: журналын мөр өөрчлөгдөхгүй/устахгүй — reversal ашиглана';
END $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_line_immutable
  BEFORE UPDATE OR DELETE ON journal_line
  FOR EACH ROW EXECUTE FUNCTION block_line_mutation();

-- G3: Түгжсэн сард бичилт орохгүй
CREATE OR REPLACE FUNCTION check_period_open() RETURNS trigger AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM period_lock
             WHERE company_id = NEW.company_id
               AND year = EXTRACT(YEAR FROM NEW.entry_date)
               AND month = EXTRACT(MONTH FROM NEW.entry_date)) THEN
    RAISE EXCEPTION 'G3: %-%.% сар түгжээтэй',
      NEW.company_id, EXTRACT(YEAR FROM NEW.entry_date), EXTRACT(MONTH FROM NEW.entry_date);
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_period_lock
  BEFORE INSERT ON journal_entry
  FOR EACH ROW EXECUTE FUNCTION check_period_open();

-- G7: Row-level security (tenant тусгаарлалт)
ALTER TABLE journal_entry ENABLE ROW LEVEL SECURITY;
ALTER TABLE journal_line  ENABLE ROW LEVEL SECURITY;
ALTER TABLE bank_txn      ENABLE ROW LEVEL SECURITY;
-- Бодлого: апп-ын role бүрд current_setting('app.company_id')-аар шүүнэ
CREATE POLICY tenant_isolation_entry ON journal_entry
  USING (company_id = current_setting('app.company_id', true));
CREATE POLICY tenant_isolation_txn ON bank_txn
  USING (company_id = current_setting('app.company_id', true));
