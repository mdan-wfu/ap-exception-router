-- reference_seed.sql
--
-- Mock master data for the Acme Corp AP system.
--
-- PROVENANCE NOTE (carry this into the README):
-- The case brief supplies only the four-row inventory table. Everything else here
-- is a reasoned extension, per the brief's invitation to "extend the seed data with
-- additional items or columns to support richer validation."
--
--   * reference_unit_price was DERIVED from the modal unit price across the invoice
--     corpus ($250 / $500 / $750). It was not supplied by the client.
--   * The vendors table stands in for what would be the ERP vendor master (SAP,
--     NetSuite) in a real engagement. It is seeded to reflect a plausible
--     established-manufacturer relationship set.
--   * Four corpus vendors are DELIBERATELY ABSENT so the unknown-vendor check has
--     teeth: Fraudster LLC, NoProd Industries, Global Supply Chain Partners,
--     QuickShip Distributers. Seeding every vendor that appears in the corpus would
--     make the check circular and useless.
--   * FastShip Ltd. is seeded as a REAL BUT INACTIVE vendor. This is the key design
--     choice: it makes INV-1012's "formerly FastShip Ltd." claim genuinely ambiguous
--     (legitimate rename vs. exploitation of a dormant relationship) rather than
--     obviously fraudulent. Ambiguity is what the escalate path exists for.

-- ---------------------------------------------------------------------------
-- INVENTORY
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS inventory (
    item                  TEXT PRIMARY KEY,
    stock                 INTEGER NOT NULL,
    reference_unit_price  NUMERIC,          -- NULL where no contract price exists
    category              TEXT,
    active                INTEGER NOT NULL DEFAULT 1
);

INSERT INTO inventory (item, stock, reference_unit_price, category, active) VALUES
    ('WidgetA',  15, 250.00, 'component', 1),
    ('WidgetB',  10, 500.00, 'component', 1),
    ('GadgetX',   5, 750.00, 'assembly',  1),
    ('FakeItem',  0, NULL,   'unknown',   0);

-- Note: FakeItem is retained from the brief's seed data. It carries stock 0 and
-- active 0, which lets the validator distinguish three distinct failure modes:
--   IN-001 unknown item      -> not present in this table at all (WidgetC, SuperGizmo)
--   IN-002 zero-stock item   -> present, active 0, stock 0 (FakeItem)
--   IN-003 quantity exceeds  -> present and active, aggregate demand > stock
-- Collapsing these into one "invalid item" finding loses information the
-- adjudicator needs.

-- ---------------------------------------------------------------------------
-- VENDORS
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS vendors (
    name                TEXT PRIMARY KEY,
    aliases             TEXT,               -- JSON array of known former/alternate names
    domain              TEXT,
    status              TEXT NOT NULL,      -- active | inactive
    contracted_terms    TEXT,               -- e.g. 'Net 30'
    relationship_since  TEXT                -- ISO date
);

INSERT INTO vendors (name, aliases, domain, status, contracted_terms, relationship_since) VALUES
    ('Widgets Inc.',                 '[]', 'widgetsinc.com',      'active',   'Net 15', '2019-03-11'),
    ('Gadgets Co.',                  '[]', 'gadgetsco.com',       'active',   'Net 30', '2020-07-22'),
    ('Precision Parts Ltd.',         '[]', 'precisionparts.com',  'active',   'Net 30', '2018-01-09'),
    ('Acme Industrial Supplies',     '[]', 'acmeindustrial.com',  'active',   'Net 15', '2017-05-30'),
    ('MegaWidgets Corp',             '[]', 'megawidgets.com',     'active',   'Net 30', '2021-11-02'),
    ('Consolidated Materials Group', '[]', 'consolidatedmg.com',  'active',   'Net 30', '2020-02-14'),
    ('Summit Manufacturing Co.',     '[]', 'summitmfg.com',       'active',   'Net 30', '2022-06-18'),
    ('Atlas Industrial Supply',      '[]', 'atlasindustrial.com', 'active',   'Net 60', '2019-09-27'),
    ('TechParts International',      '[]', 'techparts-intl.eu',   'active',   'Net 30', '2021-04-05'),
    ('Reliable Components Inc.',     '[]', 'reliablecomp.com',    'active',   'Net 30', '2023-08-16'),
    ('FastShip Ltd.',                '[]', 'fastship.com',        'inactive', 'Net 30', '2016-10-01');

-- ---------------------------------------------------------------------------
-- EXPECTED BEHAVIOUR AGAINST THE CORPUS
-- ---------------------------------------------------------------------------
--
-- Vendor findings this seed should produce:
--
--   INV-1003  Fraudster LLC                 -> VN-001 not in master
--   INV-1005  Global Supply Chain Partners  -> VN-001 not in master
--   INV-1008  NoProd Industries             -> VN-001 not in master
--                                            + VN-003 domain mismatch (noproduct.biz)
--   INV-1009  (empty vendor name)           -> VN-005 vendor name missing
--   INV-1012  QuickShip Distributers        -> VN-001 not in master
--                                            + VN-002 fuzzy candidate: FastShip Ltd.
--                                            + VN-004 inactive-vendor rename claim
--
-- Terms findings this seed should produce:
--
--   INV-1013  Atlas @ Net 60                -> matches contract, no finding
--   INV-1005  Net 60, vendor not in master  -> no contract to compare against
--   INV-1001  Widgets Inc. @ Net 15         -> matches contract; due date +17d is
--                                              within the +/-2 day tolerance
--
-- Deliberate near-misses worth noting: "Acme Industrial Supplies" (INV-1006) shares
-- a token with the buyer, "Acme Corp". A naive fuzzy matcher will flag this. It is a
-- legitimate vendor and should NOT trip VN-002. Use it as a false-positive test for
-- the fuzzy threshold.
