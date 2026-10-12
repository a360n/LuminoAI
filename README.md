# LuminoAI

Autonomous Industrial Photovoltaic EL Quality Assurance & Defect Classification Suite.

LuminoAI is an enterprise-grade artificial intelligence platform designed for high-throughput photovoltaic manufacturing facilities. It automates end-to-end solar panel electroluminescence (EL) inspection, high-resolution cell segmentation, multi-class defect classification, and distributed multi-station synchronization across production lines.

---

## Key Capabilities

- **Real-Time Automated Inspection**: Seamlessly monitors industrial image directories, instantly processing newly captured EL scans without manual intervention.
- **Precision Cell-Level Defect Analysis**: Automatically identifies micro-cracks, ribbon discrepancies, misalignment, impurities, and structural anomalies across individual solar cells.
- **Distributed Multi-Station Synchronization**: Connects Pre-Lamination and Post-Lamination inspection stations with the Central Server over secure HTTP REST architecture for unified reporting and quality analytics.
- **Digital Panel Passport**: Tracks comprehensive quality history, defect classifications, testing metrics, and rework logs linked to unique serial numbers.
- **Integrated Factory Hardware Support**: Integrates with industrial flasher simulators, electrical safety testers, and production line buzzers.

---

## Quick Start (Windows Production Environment)

### Central Server Station
To launch the primary Central Server instance:
1. Double-click `run_LuminoAI.bat` (or use the desktop shortcut `LuminoAI`).
2. Open your web browser and navigate to:
   ```
   http://localhost:8005
   ```

### Satellite Edge Station (Post-Lamination Inspection)
To launch a dedicated Satellite Edge Station:
1. Double-click `run_edge_station.bat` (or use the desktop shortcut `LuminoAI Edge Station`).
2. The station will initialize in Edge mode and automatically connect to the configured Central Server.

---

## Network & Firewall Configuration

If operating across a local factory network:
- Right-click `open_firewall_ports.bat` and select **Run as administrator** to permit inbound traffic across industrial communication ports (8005, 8006, 8007).

---

## System Requirements

- **Operating System**: Windows 10 / 11 (64-bit) or Windows Server 2019+
- **Processor**: Intel Core i5 / AMD Ryzen 5 or higher
- **Memory**: 8 GB RAM minimum (16 GB recommended)
- **Display**: 1920x1080 resolution or higher

---

## Confidentiality & License

This software and associated documentation contain proprietary intellectual property. Unauthorized copying, reverse engineering, decompilation, or distribution of this software package, in whole or in part, is strictly prohibited.

&copy; 2026 LuminoAI. All rights reserved.
