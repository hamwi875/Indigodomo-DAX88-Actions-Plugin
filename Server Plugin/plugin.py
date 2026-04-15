# -*- coding: utf-8 -*-
#
# HomeAssist DAX88 Multi-Zone Source
# plugin.py (republished to match Actions.xml with:
#   Action id: hamwi875_dax88_multi_zone_action
#   CallbackMethod: doDax88MultiZone
#   Dynamic lists: dax88ZoneList, dax88SourceList
#   Operation menu: set_source / turn_off / mute / unmute
#
# Install path:
#   HomeAssistDAX88MultiZoneSource.indigoPlugin/Contents/Server Plugin/plugin.py
#
# After editing this file:
#   Restart Indigo Server (recommended), then reopen the action config UI.
#
import indigo
import json
import time
import logging

DEFAULT_SOURCES = ["Sonos", "WiFi", "Computer", "TV", "Auxiliary", "Tuner", "Phono", "Media Server"]
SENTINEL_NONE = "none"


class Plugin(indigo.PluginBase):
    def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
        super().__init__(pluginId, pluginDisplayName, pluginVersion, pluginPrefs)

        # Optional log level (only used if you have PluginConfig.xml with logLevel)
        try:
            self.logLevel = int(pluginPrefs.get("logLevel", logging.INFO))
            self.indigo_log_handler.setLevel(self.logLevel)
            self.plugin_file_handler.setLevel(self.logLevel)
        except Exception:
            pass

        # Home Assistant Agent plugin and DAX88 entity prefix
        self.haa_plugin_id = "no.homeassistant.plugin"
        self.dax88_prefix = "media_player.xantech_dax88_"

        # Set True temporarily to spam discovery diagnostics in the log
        self.debug_discovery = False

    def startup(self):
        self.logger.info("HomeAssist DAX88 Multi-Zone Source starting up")
        self.logger.info(f"Using HAA plugin id: {self.haa_plugin_id}")
        self.logger.info(f"DAX88 HA entity prefix: {self.dax88_prefix}")

    def shutdown(self):
        self.logger.info("HomeAssist DAX88 Multi-Zone Source shutting down")

    # ---------------- UI callbacks ----------------

    def menuChanged(self, valuesDict, typeId=0, devId=0):
        return valuesDict

    def dax88ZoneList(self, _filter="", _valuesDict=None, _typeId=0, _targetId=0):
        """
        Dynamic list for Zone A..H menu fields.

        IMPORTANT: Indigo menu dynamic lists must NOT return empty-string IDs.
        We always use SENTINEL_NONE ("none") for the no-selection row.
        """
        zones = [(SENTINEL_NONE, "-- none --")]
        matched = 0

        for dev in indigo.devices:
            if self._is_dax88_zone_device(dev):
                matched += 1
                addr = self._get_haa_address(dev) or ""
                label = f"{dev.name} (id={dev.id}, addr={addr})"
                zones.append((str(dev.id), label))
            elif self.debug_discovery:
                try:
                    addr = self._get_haa_address(dev)
                    self.logger.debug(
                        f"[DAX88][skip] name={dev.name!r} id={dev.id} pluginId={dev.pluginId!r} "
                        f"typeId={dev.deviceTypeId!r} addr={addr!r}"
                    )
                except Exception:
                    pass

        zones[1:] = sorted(zones[1:], key=lambda x: (x[1].lower(), int(x[0])))

        # Keep this as INFO; it's useful to confirm the list is actually being called.
        self.logger.info(f"[DAX88] dax88ZoneList returning {matched} zone(s)")
        return zones

    def dax88SourceList(self, _filter="", valuesDict=None, _typeId=0, _targetId=0):
        """
        Dynamic list for Source menu field.
        Uses the first selected zone (A..H) to read that device's states['source_list'].
        """
        valuesDict = valuesDict or {}
        zone_id = self._get_selected_zone_for_sources(valuesDict)

        sources = list(DEFAULT_SOURCES)
        if zone_id is not None:
            sources = self._parse_source_list_from_zone(zone_id)

        return [(SENTINEL_NONE, "-- select source --")] + [(s, s) for s in sources]

    # ---------------- Validation ----------------

    def validateActionConfigUi(self, valuesDict, typeId, devId):
        errors = indigo.Dict()

        # In Actions.xml style, typeId is the Action id from Actions.xml
        if typeId != "hamwi875_dax88_multi_zone_action":
            return True, valuesDict, errors

        zone_keys = ["zoneA", "zoneB", "zoneC", "zoneD", "zoneE", "zoneF", "zoneG", "zoneH"]
        has_zone = any(valuesDict.get(k, SENTINEL_NONE) not in (SENTINEL_NONE, "", None) for k in zone_keys)
        if not has_zone:
            errors["zoneA"] = "Select at least one zone."
            return False, valuesDict, errors

        operation = valuesDict.get("operation", "set_source")

        if operation == "set_source":
            if valuesDict.get("source", SENTINEL_NONE) in (SENTINEL_NONE, "", None):
                errors["source"] = "Select a source."
                return False, valuesDict, errors

        # delayMs
        delay_raw = valuesDict.get("delayMs", "0")
        try:
            delay_ms = int(delay_raw)
            if delay_ms < 0:
                raise ValueError()
            valuesDict["delayMs"] = str(delay_ms)
        except Exception:
            errors["delayMs"] = "Enter a number of milliseconds (0 or greater)."
            return False, valuesDict, errors

        return True, valuesDict, errors

    # ---------------- Action callback ----------------

    def doDax88MultiZone(self, plugin_action, _device=None, _callerWaitingForResult=None):
        values = plugin_action.props

        haa = indigo.server.getPlugin(self.haa_plugin_id)
        if not haa.isEnabled():
            self.logger.error(f"Home Assistant Agent plugin not enabled: {self.haa_plugin_id}")
            return

        operation = values.get("operation", "set_source")
        source = values.get("source", SENTINEL_NONE)
        turn_on_first = str(values.get("turnOnFirst", "true")).lower() == "true"

        try:
            delay_ms = int(values.get("delayMs", "0"))
        except Exception:
            delay_ms = 0

        zone_keys = ["zoneA", "zoneB", "zoneC", "zoneD", "zoneE", "zoneF", "zoneG", "zoneH"]
        zone_ids = []
        for k in zone_keys:
            v = values.get(k, SENTINEL_NONE)
            if v in (SENTINEL_NONE, "", None):
                continue
            try:
                zid = int(v)
            except Exception:
                continue
            if zid not in zone_ids:
                zone_ids.append(zid)

        if not zone_ids:
            self.logger.warning("[DAX88] No zones selected; nothing to do.")
            return

        self.logger.info(
            f"[DAX88] operation={operation!r}, zones={zone_ids}, delayMs={delay_ms}, "
            f"turnOnFirst={turn_on_first}, source={source!r}"
        )

        for idx, zid in enumerate(zone_ids, start=1):
            try:
                dev = indigo.devices[zid]
            except Exception:
                self.logger.error(f"[DAX88] Zone device id {zid} not found in Indigo.")
                continue

            addr = self._get_haa_address(dev) or "unknown"
            self.logger.info(f"[DAX88] ({idx}/{len(zone_ids)}) Zone: {dev.name} (id={zid}, addr={addr})")

            try:
                if operation == "turn_off":
                    # Turn off via Indigo (routes to HAA device action)
                    indigo.device.turnOff(zid)

                elif operation == "set_source":
                    if source in (SENTINEL_NONE, "", None):
                        self.logger.error("[DAX88] No source selected.")
                        return

                    if turn_on_first:
                        indigo.device.turnOn(zid)

                    # Use HAA "hidden" action id
                    haa.executeAction(
                        "media_play_set_source",
                        deviceId=zid,
                        props={"media_source": source}
                    )

                elif operation == "mute":
                    # Uses HAA hidden action IDs (as shown in the HAA Actions.xml you pasted earlier)
                    haa.executeAction("media_player_volume_mute", deviceId=zid, props={})

                elif operation == "unmute":
                    haa.executeAction("media_player_volume_unmute", deviceId=zid, props={})

                else:
                    self.logger.error(f"[DAX88] Unknown operation: {operation}")
                    return

            except Exception as e:
                self.logger.error(f"[DAX88] Failed for zone id={zid}: {e}")

            if delay_ms > 0 and idx < len(zone_ids):
                time.sleep(delay_ms / 1000.0)

    # ---------------- Discovery helpers ----------------

    def _get_haa_address(self, dev: indigo.Device):
        """
        Return HA entity_id for an HAA-created device.
        Prefer dev.address; fallback to ownerProps[no.homeassistant.plugin]['address'].
        """
        try:
            if hasattr(dev, "address") and isinstance(dev.address, str) and dev.address:
                return dev.address
        except Exception:
            pass

        try:
            props = dev.ownerProps.get(self.haa_plugin_id, {})
            addr = props.get("address")
            if isinstance(addr, str) and addr:
                return addr
        except Exception:
            pass

        return None

    def _is_dax88_zone_device(self, dev: indigo.Device) -> bool:
        if dev.pluginId != self.haa_plugin_id:
            return False

        # Case-insensitive device type id check
        if str(dev.deviceTypeId).lower() != "ha_media_player":
            return False

        addr = self._get_haa_address(dev)
        if not isinstance(addr, str):
            return False

        return addr.lower().startswith(self.dax88_prefix.lower())

    def _get_selected_zone_for_sources(self, valuesDict):
        for key in ["zoneA", "zoneB", "zoneC", "zoneD", "zoneE", "zoneF", "zoneG", "zoneH"]:
            zid = valuesDict.get(key, SENTINEL_NONE)
            if zid in (SENTINEL_NONE, "", None):
                continue
            try:
                return int(zid)
            except Exception:
                continue
        return None

    def _parse_source_list_from_zone(self, zone_dev_id: int):
        """
        Reads zone.states['source_list'], which often arrives as a JSON string like:
          '["Sonos","WiFi", ...]'
        Returns list[str]. Falls back to DEFAULT_SOURCES.
        """
        try:
            dev = indigo.devices[zone_dev_id]
        except Exception:
            return list(DEFAULT_SOURCES)

        raw = dev.states.get("source_list")
        if not raw:
            return list(DEFAULT_SOURCES)

        if isinstance(raw, (list, tuple)):
            return [str(x) for x in raw]

        if isinstance(raw, str):
            s = raw.strip()

            # Try JSON first
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list) and parsed:
                    return [str(x) for x in parsed]
            except Exception:
                pass

            # Fallback manual parse
            if s.startswith("[") and s.endswith("]"):
                inner = s[1:-1].strip()
                if inner:
                    parts = [p.strip().strip('"').strip("'") for p in inner.split(",")]
                    parts = [p for p in parts if p]
                    if parts:
                        return parts

        return list(DEFAULT_SOURCES)