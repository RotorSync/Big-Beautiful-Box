#include <linux/spi/spidev.h>
#include <sys/ioctl.h>
#include "osal_spi.h"
#include "osal_log.h"
#include "options.h"
#include "fcntl.h"
#include <stdio.h>
#include "unistd.h"
#include <string.h>

int _iolink_pl_hw_spi_init (const char * spi_slave_name)
{
   int fd = -1;
   int mode = 0;
   int bits = 8;
   int speed = 5000000;

   fd = open (spi_slave_name, O_RDWR);
   if (fd == -1)
   {
      LOG_ERROR (IOLINK_PL_LOG, "Failed to open SPI device: %s\n", spi_slave_name);
      return -1;
   }

   if (ioctl(fd, SPI_IOC_WR_MODE, &mode) < 0) {
      LOG_ERROR (IOLINK_PL_LOG, "Failed to set SPI mode\n");
      close(fd);
      return -1;
   }

   if (ioctl(fd, SPI_IOC_WR_BITS_PER_WORD, &bits) < 0) {
      LOG_ERROR (IOLINK_PL_LOG, "Failed to set SPI bits per word\n");
      close(fd);
      return -1;
   }

   if (ioctl(fd, SPI_IOC_WR_MAX_SPEED_HZ, &speed) < 0) {
      LOG_ERROR (IOLINK_PL_LOG, "Failed to set SPI speed\n");
      close(fd);
      return -1;
   }

   LOG_INFO (IOLINK_PL_LOG, "SPI initialized: %s (mode=%d, speed=%d Hz)\n", spi_slave_name, mode, speed);

   return fd;
}

void _iolink_pl_hw_spi_close (int fd)
{
   close (fd);
}

void _iolink_pl_hw_spi_transfer (
   int fd,
   void * data_read,
   const void * data_written,
   size_t n_bytes_to_transfer)
{
   int spi_fd = fd;

   int delay = 10;
   int speed = 5000000;
   int bits  = 8;

   struct spi_ioc_transfer tr;
		memset(&tr, 0, sizeof (tr));

		tr.tx_buf        = (unsigned long)data_written;
		tr.rx_buf        = (unsigned long)data_read;
		tr.len           = n_bytes_to_transfer;
		tr.delay_usecs   = delay;
		tr.speed_hz      = speed;
		tr.bits_per_word = bits;


   if (ioctl (spi_fd, SPI_IOC_MESSAGE (1), &tr) < 1)
   {
      LOG_ERROR (IOLINK_PL_LOG, "%s: failed to send SPI message\n", __func__);
   }
}
