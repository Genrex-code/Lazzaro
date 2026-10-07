using System;
using UnityEngine;

[Serializable]
public sealed class PerceptionCaptureContext
{
    public string producer_instance_id;
    public string stream_id;
    public string producer_epoch_id;
    public string logical_world_id;
    public string producer_capture_id;
    public long generation;
    public long captured_at_utc_ms;
}

[DisallowMultipleComponent]
public class PerceptionLifecycleContext : MonoBehaviour
{
    [SerializeField]
    [Tooltip("Identidad estable y unica de este productor real. Debe configurarse; nunca se genera por proceso.")]
    private string producerInstanceId = "";

    [SerializeField]
    [Tooltip("Stream code-owned del productor perceptivo.")]
    private string streamId = "unity-world-perception";

    [SerializeField]
    [Tooltip("Namespace logico persistente opcional. No usar el nombre de Scene.")]
    private string logicalWorldId = "";

    private readonly object captureLock = new object();
    private string producerEpochId;
    private long nextGeneration;

    public string ProducerInstanceId => (producerInstanceId ?? "").Trim();
    public string StreamId => (streamId ?? "").Trim();
    public string ProducerEpochId => producerEpochId ?? "";
    public string LogicalWorldId => string.IsNullOrWhiteSpace(logicalWorldId)
        ? null
        : logicalWorldId.Trim();
    public long LatestIssuedGeneration
    {
        get
        {
            lock (captureLock)
            {
                return nextGeneration == 0 ? -1 : nextGeneration - 1;
            }
        }
    }

    void Awake()
    {
        producerEpochId = Guid.NewGuid().ToString("D");
        nextGeneration = 0;
    }

    public PerceptionCaptureContext BeginCapture()
    {
        lock (captureLock)
        {
            long generation = nextGeneration;
            if (nextGeneration == long.MaxValue)
            {
                throw new InvalidOperationException("Perception generation exhausted for epoch.");
            }
            nextGeneration++;
            return new PerceptionCaptureContext
            {
                producer_instance_id = ProducerInstanceId,
                stream_id = StreamId,
                producer_epoch_id = ProducerEpochId,
                logical_world_id = LogicalWorldId,
                producer_capture_id = Guid.NewGuid().ToString("D"),
                generation = generation,
                captured_at_utc_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
            };
        }
    }
}
