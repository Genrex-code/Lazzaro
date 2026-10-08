using System;
using System.Collections.Generic;
using System.Globalization;
using UnityEngine;

public enum ExactTargetStatus
{
    Resolved,
    InvalidReference,
    ProducerMismatch,
    EpochMismatch,
    WorldMismatch,
    TargetMissing,
    DuplicateRuntimeIdentity,
    PersistentIdentityMismatch,
}

public sealed class ExactTargetResolution
{
    public ExactTargetStatus Status { get; private set; }
    public Collider Target { get; private set; }
    public string ReasonCode { get; private set; }

    public bool IsResolved => Status == ExactTargetStatus.Resolved && Target != null;

    public ExactTargetResolution(ExactTargetStatus status, Collider target, string reasonCode)
    {
        Status = status;
        Target = target;
        ReasonCode = reasonCode;
    }
}

[RequireComponent(typeof(PerceptionSystem))]
[RequireComponent(typeof(PerceptionLifecycleContext))]
public class ExactTargetResolver : MonoBehaviour
{
    private PerceptionSystem perception;
    private PerceptionLifecycleContext lifecycle;

    void Awake()
    {
        perception = GetComponent<PerceptionSystem>();
        lifecycle = GetComponent<PerceptionLifecycleContext>();
    }

    public ExactTargetResolution Resolve(TargetReferenceV1 reference)
    {
        if (
            reference == null
            || reference.contract_version != 1
            || string.IsNullOrWhiteSpace(reference.runtime_object_id)
        )
        {
            return Failure(ExactTargetStatus.InvalidReference, "target_reference_invalid");
        }
        if (reference.producer_instance_id != lifecycle.ProducerInstanceId)
        {
            return Failure(ExactTargetStatus.ProducerMismatch, "producer_instance_mismatch");
        }
        if (reference.stream_id != lifecycle.StreamId)
        {
            return Failure(ExactTargetStatus.ProducerMismatch, "stream_mismatch");
        }
        if (reference.producer_epoch_id != lifecycle.ProducerEpochId)
        {
            return Failure(ExactTargetStatus.EpochMismatch, "producer_epoch_mismatch");
        }
        if (
            reference.logical_world_id != null
            && reference.logical_world_id != lifecycle.LogicalWorldId
        )
        {
            return Failure(ExactTargetStatus.WorldMismatch, "logical_world_mismatch");
        }

        HashSet<GameObject> exactObjects = new HashSet<GameObject>();
        Collider target = null;
        foreach (Collider candidate in perception.ObtenerObjetosInteractuables())
        {
            if (candidate == null)
            {
                continue;
            }
            string runtimeId = candidate.gameObject.GetInstanceID().ToString(
                CultureInfo.InvariantCulture
            );
            if (runtimeId == reference.runtime_object_id)
            {
                if (exactObjects.Add(candidate.gameObject) && target == null)
                {
                    target = candidate;
                }
            }
        }
        if (exactObjects.Count == 0)
        {
            return Failure(ExactTargetStatus.TargetMissing, "exact_target_missing");
        }
        if (exactObjects.Count != 1)
        {
            return Failure(
                ExactTargetStatus.DuplicateRuntimeIdentity,
                "duplicate_runtime_object_id"
            );
        }

        if (!string.IsNullOrWhiteSpace(reference.producer_object_id))
        {
            InteractableMetadata metadata = target.GetComponent<InteractableMetadata>();
            string producerId = metadata != null ? metadata.GetExplicitObjectId() : null;
            if (producerId != reference.producer_object_id)
            {
                return Failure(
                    ExactTargetStatus.PersistentIdentityMismatch,
                    "producer_object_id_mismatch"
                );
            }
        }
        return new ExactTargetResolution(ExactTargetStatus.Resolved, target, "exact_target_resolved");
    }

    public bool IsGenerationBindingValid(TargetReferenceV1 reference, string bindingMode)
    {
        if (reference == null || string.IsNullOrWhiteSpace(bindingMode))
        {
            return false;
        }
        long latest = lifecycle.LatestIssuedGeneration;
        if (latest < 0)
        {
            return false;
        }
        switch (bindingMode)
        {
            case "REQUIRE_AUTHORIZED_GENERATION":
                return latest == reference.generation;
            case "REJECT_IF_KNOWN_SUPERSEDED":
                return latest <= reference.generation;
            case "ALLOW_ADVANCE_WITH_EXACT_TARGET":
                return latest >= reference.generation;
            default:
                return false;
        }
    }

    private static ExactTargetResolution Failure(ExactTargetStatus status, string reasonCode)
    {
        return new ExactTargetResolution(status, null, reasonCode);
    }
}
