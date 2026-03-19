# Planilla CR - Módulo de Planillas para Odoo (Costa Rica)

## Descripción
Módulo completo de gestión de planillas adaptado a la legislación costarricense.

## Características Principales

### Configuración
- **Sucursales**: soporte multi-sucursal
- **Tipos de Identificación**: Cédula, DIMEX, Pasaporte, etc.
- **Estados de Empleado**: activo, vacaciones, licencia, etc.
- **Tipos de Empleado**: indefinido, plazo fijo, servicios profesionales, etc.
- **Tipos de Horario**: horas por día/semana, factor horas extras
- **Calendarizaciones**: semanal, quincenal, mensual
- **Códigos de Deducción**: CCSS, INS, Renta, Aguinaldo, Cesantía, Vacaciones

### Empleados (extensión de hr.employee)
- Pestaña "Planilla CR" con todos los campos locales
- Datos de CCSS, cuenta bancaria, fechas de ingreso/salida
- Historial de salarios automático al modificar salario base

### Novedades
- **Horas Extras**: simple (1.5x), doble (2x), feriado
- **Incapacidades**: CCSS, INS, maternidad — cálculo automático de subsidio
- **Vacaciones**: cálculo de días acumulados según antigüedad

### Planilla
- Generación masiva de boletas por calendarización/sucursal
- Cálculo automático de CCSS, Renta (tabla progresiva MH), cargas patronales
- Flujo: Borrador → Confirmado → Pagado
- Asiento contable automático al pagar

### Boleta de Pago
- Impresión en PDF con diseño profesional
- Envío automático por correo al empleado
- Líneas de deducción adicionales personalizadas

### Historial
- Registro automático por cada planilla pagada
- Impresión de historial salarial por colaborador

## Integración con módulos Odoo
- `hr_holidays` (Ausencias)
- `hr_attendance` (Asistencia)
- `hr_recruitment` (Reclutamiento)
- `hr_appraisal` (Evaluación de Personal)
- `hr_expense` (Gastos)
- `account` (Contabilidad)

## Tasas legales CR (2026)

### Deducciones Obrero
| Concepto | % |
|---|---|
| CCSS Obrero | 10.83% |
| ROP Obrero (Ley 7983) | 1.00% |
| Impuesto Renta | Tabla progresiva |

### Cargas Patronales
| Concepto | % |
|---|---|
| CCSS Patronal | 26.83% |
| ROP Patronal (Ley 7983) | 3.25% |
| INS (varía por clase de riesgo) | 0.87% – 6.88% |
| Provisión Aguinaldo | 8.33% |
| Provisión Cesantía | 5.33% |
| Provisión Vacaciones | 4.16% |

### Tabla de Renta (salarios mensuales 2026 — DGT-R-016-2026)
| Rango | Tasa |
|---|---|
| Hasta ₡941,000 | Exento |
| ₡941,001 - ₡1,381,000 | 10% |
| ₡1,381,001 - ₡2,423,000 | 15% |
| ₡2,414,001 - ₡4,830,000 | 20% |
| Más de ₡4,830,000 | 25% |

## Instalación
1. Copiar carpeta `planilla_cr` a `/addons` de tu instancia Odoo
2. Activar modo desarrollador
3. Actualizar lista de módulos
4. Instalar "Planilla Costa Rica"

## Dependencias
```
hr, hr_attendance, hr_holidays, hr_recruitment,
hr_appraisal, hr_expense, account, mail
```
