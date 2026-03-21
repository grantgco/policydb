-- Add mobile phone field to contact tables; migrate existing phone values to mobile
ALTER TABLE client_contacts ADD COLUMN mobile TEXT;
UPDATE client_contacts SET mobile = phone;

ALTER TABLE policy_contacts ADD COLUMN mobile TEXT;
UPDATE policy_contacts SET mobile = phone;

-- clients table already has contact_phone as the main phone; add a mobile field
ALTER TABLE clients ADD COLUMN contact_mobile TEXT;
UPDATE clients SET contact_mobile = contact_phone;
