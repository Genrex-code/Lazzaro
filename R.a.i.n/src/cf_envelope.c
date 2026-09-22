#include <stdlib.h>
#include "cf_envelope.h"

cf_status_t cf_expression_envelope_validate(const cf_expression_envelope_t* envelope) {
    if (!envelope) return CF_INVALID_ARGUMENT;
    
    /* Si la versión del ABI no coincide, fallamos cerrado para evitar corrupción de memoria */
    if (envelope->header.abi_version != 1) return CF_CONTRACT_VIOLATION;
    
    /* El LLM no puede recibir un envelope sin hechos o restricciones claras */
    if (!envelope->assertable_facts && !envelope->allowed_interpretations) {
        return CF_CONTRACT_VIOLATION;
    }
    
    return CF_OK;
}

void cf_envelope_destroy(cf_expression_envelope_t* envelope) {
    if (envelope) {
        if (envelope->assertable_facts) free(envelope->assertable_facts);
        if (envelope->allowed_interpretations) free(envelope->allowed_interpretations);
        free(envelope);
    }
}
