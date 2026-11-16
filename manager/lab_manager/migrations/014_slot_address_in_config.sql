-- A slot's address is unique only among the slots of the slots file. A slot removed from the file keeps its row
-- (sessions reference its VMID) and must not block its address: renumbered slots reuse the old IPs.
ALTER TABLE slots DROP CONSTRAINT slots_address_key;
CREATE UNIQUE INDEX slots_address_in_config ON slots (address) WHERE in_config;
