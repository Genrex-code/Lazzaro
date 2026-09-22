#ifndef CF_RECEIPTS_H
#define CF_RECEIPTS_H

#include <stdint.h>
#include "cf_types.h"

/* 
 * 5.2 Assurance Level (TEARS)[cite: 11].
 * Define la certeza de la experiencia. No todo lo reportado es materialmente cierto.
 */
typedef enum {
    CF_ASSURANCE_UNKNOWN = 0,
    CF_ASSURANCE_OBSERVED = 1,
    CF_ASSURANCE_MATERIAL_ATTESTED = 2,
    CF_ASSURANCE_REPORTED = 3,
    CF_ASSURANCE_DERIVED = 4
} cf_assurance_level_t;

/*
 * 5.2 ExperienceReceipt (De T.E.A.R.S.)[cite: 11].
 * Significa: "Existe evidencia validada para que esta experiencia entre al sistema"[cite: 11].
 */
typedef struct {
    uint64_t receipt_id;
    uint64_t occurred_at;
    cf_assurance_level_t assurance;
    float confidence; /* Rango 0.0 a 1.0 */
    
    /* 5.3 Inmutabilidad y contradicción: Si un nuevo hecho desmiente al anterior[cite: 11] */
    uint8_t contradicts_previous; 
} cf_experience_receipt_t;

#endif // CF_RECEIPTS_H
