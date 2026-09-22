#ifndef CF_ENVELOPE_H
#define CF_ENVELOPE_H

#include <stdint.h>
#include "cf_types.h"
#include "cf_expression_gate.h"

/* 
 * 12.2 ExpressionEnvelope: Entregado a Persona (Zona C)[cite: 7].
 * Revelamos la estructura opaca para que el adapter de Python (vía ctypes/cffi) 
 * pueda leer estos campos antes de inyectarlos al prompt del LLM.
 */
struct cf_expression_envelope {
    /* 14.10 Metadatos de ABI para cruzar hacia Python con seguridad[cite: 7] */
    cf_expression_envelope_v1_t header;

    /* Límites de modalidad impuestos por el Atrapamoscas (T2) */
    cf_allowed_modality_t required_modality;
    
    /* 9.8 Fricción Cognitiva: Si hubo conflicto real, el LLM tiene derecho a actuar confundido[cite: 7] */
    uint8_t cognitive_friction_level; 
    
    /* Grado de libertad social (1 = alto sarcasmo/humor permitido, 0 = restringido) */
    uint8_t social_freedom_high;

    /* Cadenas validadas (se asume que T2 ya purgó lo que no es cierto) */
    char* assertable_facts;
    char* allowed_interpretations;
    
    /* Máscara de bits para clases de claims prohibidos (Ej: Observación de sensores) */
    uint32_t prohibited_claim_classes;
};

/* 15.4 Validación final antes de entregar el control al LLM[cite: 7] */
cf_status_t cf_expression_envelope_validate(const cf_expression_envelope_t* envelope);

/* Destruye el envelope tras ser consumido por la Zona C */
void cf_envelope_destroy(cf_expression_envelope_t* envelope);

#endif // CF_ENVELOPE_H
