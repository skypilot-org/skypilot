import React, { useState, useEffect } from 'react';
import { CircularProgress } from '@mui/material';
import { useRouter } from 'next/router';
import Link from 'next/link';
import Head from 'next/head';
import { RotateCwIcon } from 'lucide-react';
import { useServiceDetail } from '@/data/connectors/services';
import { Card } from '@/components/ui/card';
import { useMobile } from '@/hooks/useMobile';
import { checkGrafanaAvailability } from '@/utils/grafana';
import { ServiceOverview } from '@/components/services/ServiceOverview';
import { ReplicasTable } from '@/components/services/ReplicasTable';
import { ServiceMetrics } from '@/components/services/ServiceMetrics';
import { ServiceLogs } from '@/components/services/ServiceLogs';
import { ServicePlayground } from '@/components/services/ServicePlayground';

function ServiceDetails() {
  const router = useRouter();
  const { service } = router.query;
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [refreshTrigger, setRefreshTrigger] = useState(0);
  const [isGrafanaAvailable, setIsGrafanaAvailable] = useState(false);
  const [activeTab, setActiveTab] = useState('overview');
  const isMobile = useMobile();

  const { serviceData, loading, refresh } = useServiceDetail(
    service,
    refreshTrigger
  );

  useEffect(() => {
    const checkGrafana = async () => {
      const available = await checkGrafanaAvailability();
      setIsGrafanaAvailable(available);
    };
    checkGrafana();
  }, []);

  const handleRefresh = async () => {
    setIsRefreshing(true);
    setRefreshTrigger((prev) => prev + 1);
    await refresh();
    setIsRefreshing(false);
  };

  if (!router.isReady) {
    return null;
  }

  const tabs = [
    { id: 'overview', label: 'Overview' },
    { id: 'metrics', label: 'Metrics' },
    { id: 'logs', label: 'Logs' },
    { id: 'playground', label: 'Playground' },
  ];

  return (
    <>
      <Head>
        <title>
          {service ? `${service} | Services` : 'Service'} | SkyPilot Dashboard
        </title>
      </Head>

      {/* Breadcrumbs */}
      <div className="flex items-center justify-between mb-4 h-5">
        <div className="text-base">
          <Link
            href="/services"
            className="text-sky-blue hover:underline leading-none"
          >
            Services
          </Link>
          <span className="mx-2 text-gray-400">/</span>
          <span className="text-gray-700 leading-none">{service}</span>
        </div>
        <div className="flex items-center gap-3">
          {(loading || isRefreshing) && (
            <div className="flex items-center">
              <CircularProgress size={15} />
              <span className="ml-2 text-gray-500 text-sm">Loading...</span>
            </div>
          )}
          <button
            onClick={handleRefresh}
            disabled={loading || isRefreshing}
            className="text-sky-blue hover:text-sky-blue-bright flex items-center"
          >
            <RotateCwIcon className="h-4 w-4 mr-1.5" />
            {!isMobile && <span>Refresh</span>}
          </button>
        </div>
      </div>

      {loading && !serviceData ? (
        <div className="flex justify-center items-center py-20">
          <CircularProgress size={30} />
          <span className="ml-3 text-gray-500">Loading service details...</span>
        </div>
      ) : !serviceData ? (
        <Card className="p-6 text-center text-gray-500">
          Service &quot;{service}&quot; not found.
        </Card>
      ) : (
        <>
          {/* Tab Navigation */}
          <div className="flex border-b border-gray-200 mb-4">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                  activeTab === tab.id
                    ? 'border-sky-blue text-sky-blue'
                    : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          {/* Tab Content */}
          {activeTab === 'overview' && (
            <>
              <ServiceOverview serviceData={serviceData} />
              <div className="mt-6">
                <ReplicasTable replicas={serviceData.replica_info} />
              </div>
            </>
          )}

          {activeTab === 'metrics' && (
            <ServiceMetrics
              serviceData={serviceData}
              isGrafanaAvailable={isGrafanaAvailable}
              refreshTrigger={refreshTrigger}
            />
          )}

          {activeTab === 'logs' && (
            <ServiceLogs
              serviceName={serviceData.name}
              replicas={serviceData.replica_info}
              refreshTrigger={refreshTrigger}
            />
          )}

          {activeTab === 'playground' && (
            <ServicePlayground serviceData={serviceData} />
          )}
        </>
      )}
    </>
  );
}

export default ServiceDetails;
