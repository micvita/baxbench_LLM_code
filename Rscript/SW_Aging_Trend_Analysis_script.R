library(readr)
library(trend)
library(zyp)
library(randtests)
library(ggplot2)
library(xts)
library(zoo)
library(highfrequency)
library(TSstudio)
library(modifiedmk)


#TREND ANALYSIS FUNCTION
#################### INPUTS: ####################################
#exclude_list:  a list of the columns in the csv file that need to be excluded from the analysis, because of constant values or other reasons.
#csv_path:      path of the csv file to be examined.
#task_name:     name of the task (e.g. monitor, UpTimeService, ...)
#env_name:      name of the enviroment (e.g python_django, python_fastapi, ...)
#aggregate:     logical value, TRUE means that all the time series in the csv file need to be aggregated.
#aggr_FUN:      function used to aggregate the values inside the chosen interval.
#aggr_align:    specifies the interval bucket used for data aggregation. Default value is "minutes".
#aggr_period:   specifies the interval bucket dimension. Default is 1, so leaving all default values
#               aggregates data in 1 minute interval buckets.
#dt_position:   the time index position in the csv file in input (column number)
#tz:            timezone used in the aggregate function. Needed for timestamp UNIX conversion.
##################################################################

trend_analysis <- function(exclude_list = c(1), csv_path, task_name, env_name, aggregate = FALSE, dt_position = 1,
                            aggr_FUN = "median", aggr_align = "minutes", aggr_period = 1, plot_dir, tz = "America/Sao_Paulo") {
  
  print(paste("We are currently analyzing the following dataset:", basename(csv_path)))                            

  #1. Read the data from the csv file using read_csv from the readr package.
  datain <- read_csv(csv_path)

  #Rows parsed incorrectly are logged
  parse_issues <- problems(datain)

  if (nrow(parse_issues) > 0) {
    print(paste("Parsing problems found in:", basename(csv_path)))
    print(parse_issues)
    if(aggregate) {
      #malformed rows are usally in client csv
      datain <- datain[stats::complete.cases(datain), ]
    }
  } else {
    print(paste("No parsing problems found in:", basename(csv_path)))
  }

  #Width of the dataframe datain (indicates the number of variables to analyze)
  width  <- ncol(datain)

  #Temporary dataframe to save the results of the trend analysis
  dataout <- data.frame(task = character(), env = character(), parameter = character(), cox_stuart_statistic = numeric(),
    cox_stuart_pvalue = numeric(), rho_spearman = numeric(), spearman_pvalue = numeric(), tau_kendall = numeric(), 
    mann_kendall_pvalue = numeric(), mann_kendall_HR_corrected = numeric(), trend_detected = logical(), sen_slope = numeric(),
    slope_upperCI_95 = numeric(), slope_lowerCI_95 = numeric(), sen_intercept = numeric(), 
    intercept_upperCI_95 = numeric(), intercept_lowerCI_95 = numeric())

  #2. We cycle through all the variables, minus the excluded ones, to test for trend.
  for (col in setdiff(1:width, exclude_list)) {
  
    #Print in the log file the current iteration
    print(paste("We are currently analyzing the following variable:", colnames(datain)[col]))

    #If aggregate in input == TRUE then we need to aggregate the timestamps of the csv file
    if (aggregate) {
      vut_ts <- aggregate_fun(datain[[col]], datain[[dt_position]], aggr_FUN, aggr_align, aggr_period, tz)
    } else {
      #Create time series object with xts package without aggregation
      vut_ts <- xts(datain[[col]], datain[[dt_position]])
    }

    #current data ("variable under test" vut)
    vut    <- as.numeric(coredata(vut_ts))
    #time index
    date   <- index(vut_ts)

    #Deleting samples with NA
    #keep is a vector of logical values where FALSE points to a missing timestamp or value of vut
    keep <- !is.na(vut) & !is.na(date)
    #We keep only the values and timestamps that are not NA inside the vut and date vectors
    vut  <- vut[keep]
    date <- date[keep]

    #Skip vut if constant
    if (length(unique(vut)) < 2) {
      print(paste("Skipping", colnames(datain)[col], ": constant series"))
      next
    }
  
    #If the data has seasonality characteristics you have to set the the frequency m.
    #The data cycles every m minutes. To see an example: Analysis of Software Aging in a Web Server, Michael Grottke
    #m <- season_freq
    #vut_ts_s <- ts(vut, start = c(1, 1), frequency = m)

    #3. Cox Stuart Trend test on vut_ts
    #Selecting "two sided" the null hypothesis is tested against either
    #an upward trend or an downward trend
    cs_res <- cox.stuart.test(vut, "two.sided")
    #Save the result in a log file by printing it on the R console
    print(cs_res)
    print(paste("Cox-Stuart test p-value for", colnames(datain)[col], "is:", cs_res$p.value))

    #4. Spearman rho rank correlation
    #as.numeric(date) transform POSIXct datetime format to timestamp.
    #spearman works on ranks so the transformation does not invalidate the rho computation.
    sr_res <- cor.test(vut, as.numeric(date), alternative = "two.sided",
                       method = "spearman", exact = FALSE)
    #Save the result in a log file
    print(sr_res)
    print(paste("Spearman test p-value for", colnames(datain)[col], "is:", sr_res$p.value))

    #5. "Classic" Mann Kendall trend test
    mk_res <- mk.test(vut)
    #Save the result of the mk test on the log file
    print(mk_res)
    print(paste("Mann-Kendall test p-value for", colnames(datain)[col], "is:", mk_res$p.value))
    #Autocorrelation corrected mk test for more accurate pvalue in this context
    mk_mod_res <- mmkh(vut)
    print(mk_mod_res)
    mk_mod_pvalue <- mk_mod_res[["new P-value"]]
    print(paste("Hamed-Rao Mann-Kendall test p-value for", colnames(datain)[col], "is:", mk_mod_pvalue))

    #6. If at least 2 out of the 3 tests hint to a monotonic trend with a
    #pvalue < 0.05, we compute the slope with Theil Sen estimator.
    #trend_sig contains the logical value TRUE if a trend is detected, FALSE if there is no trend.
    
    # trend_sig <- !is.na(mk_res$p.value) &&
    #   !is.na(cs_res$p.value) &&
    #   !is.na(sr_res$p.value) && (
    #   (mk_res$p.value < 0.05 && cs_res$p.value < 0.05) ||
    #     (cs_res$p.value < 0.05 && sr_res$p.value < 0.05) ||
    #     (sr_res$p.value < 0.05 && mk_res$p.value < 0.05)
    # )

    #I modified it to make it more robust to autocorrelation, so now if Mann Kendall detects a trend
    #and another supporting test statistic also finds a trend then TREND DETECTED. If Mann Kendall 
    #corrected pvalue > 0.05 then NO TREND DETECTED a priori.
    trend_sig <- !is.na(mk_mod_pvalue) &&
      !is.na(cs_res$p.value) &&
      !is.na(sr_res$p.value) &&
      mk_mod_pvalue < 0.05 &&
      (cs_res$p.value < 0.05 || sr_res$p.value < 0.05)

    # Observation index
    # vut needs to be sampled at regular intervals (e.g. every minute)
    x_time <- as.numeric(difftime(date, min(date), units = "mins"))

    if (trend_sig) {

      print("Trend DETECTED! Computing slope ...")

      #7. Theil-sen estimator 95% confidence interval
      sslope   <- zyp.sen(vut ~ x_time)
      ci_slope <- confint.zyp(sslope, level = 0.95)
      print(sslope)
      print(ci_slope)

      slope     <- unname(sslope$coefficients[2])
      intercept <- unname(sslope$coefficients[1])

      # Confidence-interval slopes
      slope_lci     <- unname(ci_slope[2])
      slope_uci     <- unname(ci_slope[4])
      intercept_lci <- unname(ci_slope[1])
      intercept_uci <- unname(ci_slope[3])
      
      #Plot
      plot_fun(trend_sig, sslope, ci_slope, vut, date, x_time, datain, col, plot_dir)

      #printing pvalues of all tests in the output dataframe
      dataout[nrow(dataout) + 1, ] = c(
        task_name, env_name, colnames(datain)[col],
        cs_res$statistic, cs_res$p.value, sr_res$estimate, sr_res$p.value,
        mk_res$estimates[3], mk_res$p.value, mk_mod_pvalue, trend_sig,
        slope, slope_uci, slope_lci, intercept, intercept_uci, intercept_lci
      )

    } else {

      print("NO Trend DETECTED!")

      #Add row with no values for theil sen estimator because there was no significant trend
      dataout[nrow(dataout) + 1, ] = c(task_name, env_name,
        colnames(datain)[col], cs_res$statistic,
        cs_res$p.value, sr_res$estimate,
        sr_res$p.value, mk_res$estimates[3],
        mk_res$p.value, mk_mod_pvalue, trend_sig,
        NA_real_, NA_real_, NA_real_, NA_real_, NA_real_, NA_real_
      )

       plot_fun(trend_sig = trend_sig, vut = vut, date = date, x_time = x_time, datain = datain, col = col, output_dir = plot_dir)

    }
  }
  
  return(dataout)
}

#PLOT FUNCTION
#################### INPUTS: ####################################
#sslope:    output of zyp.sen
#ci_slope:  output of confint.zyp
#datain:    dataframe with all the time series to analyze
#col:       index that point to the column/time series currently under test
#data:      data for plot. Needs to be in the following format:
#               data <- data.frame(
#                 date = date,
#                 x    = seq_along(vut),
#                 vut  = vut
#                )
#           where vut is the list containing the values of the time series
#           and date is the list containing the datetime/timestamps associated.
#output_dir: the directory where all the plots are going to be saved.
##################################################################

plot_fun <- function(trend_sig, sslope, ci_slope, vut, date, x_time, datain, col, output_dir) {

        #Data we need for plotting the regression line
        data <- data.frame(
          date = date,
          x    = x_time,
          vut  = vut
        )

        #First we "sanitize" the column names for the svg plot title
        fname <- gsub("[^A-Za-z0-9_-]", "_", colnames(datain)[col])

        file_name <- paste0("plot_", fname, ".svg")
        file_path <- file.path(output_dir, file_name)

        svg(file_path, width = 15, height = 5)
        on.exit(dev.off(), add = TRUE)

        if(trend_sig) {

          if(missing(sslope) || missing(ci_slope)) {
            stop("No slope for Regression line plotting!")
          }

          #unname is used to get only numeric values and not any attribute linked to the variable
          slope     <- unname(sslope$coefficients[2])
          intercept <- unname(sslope$coefficients[1])

          #Confidence-interval slopes
          slope_lci     <- unname(ci_slope[2])
          slope_uci     <- unname(ci_slope[4])

          #Corresponding intercepts
          #we compute the intercept for the slope upper boundary and lower boundary
          intercept_lci_plot <- median(data$vut - slope_lci * data$x, na.rm = TRUE)
          intercept_uci_plot <- median(data$vut - slope_uci * data$x, na.rm = TRUE)

          #Fitted lines
          data$sen_fit     <- intercept          + slope     * data$x
          data$sen_fit_lci <- intercept_lci_plot + slope_lci * data$x
          data$sen_fit_uci <- intercept_uci_plot + slope_uci * data$x

          #Scatter plot + Regression line using geom_line (ggplot2 library)
          ggp <- ggplot(data, aes(x = date, y = vut)) +
            geom_point(show.legend = FALSE) +
            geom_line(
              aes(y = sen_fit, color = "Estimated Trend", linetype = "Estimated Trend"),
              linewidth = 0.9
            ) +
            geom_line(
              aes(y = sen_fit_lci, color = "95% CI Lower", linetype = "95% CI Lower"),
              linewidth = 0.2
            ) +
            geom_line(
              aes(y = sen_fit_uci, color = "95% CI Upper", linetype = "95% CI Upper"),
              linewidth = 0.2
            ) +
            scale_color_manual(
              name = "",
              values = c(
                "Estimated Trend" = "red",
                "95% CI Lower" = "green",
                "95% CI Upper" = "blue"
              )
            ) +
            scale_linetype_manual(
              name = "",
              values = c(
                "Estimated Trend" = "solid",
                "95% CI Lower" = "dashed",
                "95% CI Upper" = "dashed"
              )
            ) +
            scale_x_datetime(
              date_breaks = "2 hour",
              date_labels = "%H:%M"
            ) +
            labs(
              title = paste(colnames(datain)[col], "Time series"),
              x = "Time (hours)",
              y = colnames(datain)[col]
            ) +
            theme_light() +
            theme(
              axis.text.x = element_text(angle = 45, hjust = 1)
            )
        } else {

          #Scatter plot (ggplot2 library)
          ggp <- ggplot(data, aes(x = date, y = vut)) +
            geom_point(show.legend = FALSE) +
            scale_x_datetime(
              date_breaks = "2 hour",
              date_labels = "%H:%M"
            ) +
            labs(
              title = paste(colnames(datain)[col], "Time series"),
              x = "Time (hours)",
              y = colnames(datain)[col]
            ) +
            theme_light() +
            theme(
              axis.text.x = element_text(angle = 45, hjust = 1)
            )
        }

        print(ggp)
}

#AGGREGATE FUNCTION
#################### INPUTS: ####################################
#vut:             list of the time series values currently under test.
#timestamp:       list containing all the timestamps that map to vut values.
#UNIX_ms_convert: the timestamps could be in seconds or ms. Currently I need to convert from ms.
#                 Change to 1 if the timestamps are in s.
#
# The other arguments are discussed in Trend Analysis Function.
#################################################################

aggregate_fun <- function(vut, timestamp, FUN, align, period, tz, UNIX_ms_convert = 1000) {
  ts_xts          <- xts(vut, as.POSIXct(as.numeric(timestamp)/UNIX_ms_convert, origin = "1970-01-01", tz = tz))
  ts_xts_agg      <- aggregateTS(ts_xts, FUN = FUN, alignBy = align, alignPeriod = period, tz = tz)
  return (ts_xts_agg)
}

#MAIN
############################################################
#server_csv:            pass the absolute path of the csv file that contains server collected data
#client_csv:            pass the absolute path of the csv file that contains jmeter/client collected data
#task_name:             pass the name of the task (e.g. monitor, ImageConverter, ...)
#env_name:              pass language + framework (e.g. python_django, python_flask, ...)
#exclude_server_cols:   CHANGE IT if you need to exclude columns of the file from the analysis
#exclude_client_cols:   CHANGE IT if you need to exclude columns of the file from the analysis 

main <- function(...) {
  config <- list(
    output_dir = file.path(getwd(), "Rscript/csv_data_analysis_results"),
    logs_dir   = file.path(getwd(), "Rscript/logs"),
    plot_dir   = file.path(getwd(), "Rscript/plots"),
    exclude_server_cols = c(1),                   #columns of the csv file to exclude from the analysis
    exclude_client_cols = c(1),
    tz = "America/Sao_Paulo"                      #timeZone for timestamp conversion
  )

  args <- commandArgs(trailingOnly = TRUE)

  if (length(args) < 4 || length(args) > 4) {
    stop("Check args! The cmd should be in the following format: Rscript script.R <server_csv> <client_csv> <task_name> <env_name>")
  }

  server_csv <- args[1]
  client_csv <- args[2]
  task_name  <- args[3]
  env_name   <- args[4]

  path_plots <- file.path(config$plot_dir, paste(env_name, task_name, sep = "_"))

  dir.create(config$output_dir, recursive = TRUE, showWarnings = FALSE)
  dir.create(path_plots, recursive = TRUE, showWarnings = FALSE)
  dir.create(config$logs_dir, recursive = TRUE, showWarnings = FALSE)

  log_path <- file.path(
    config$logs_dir,
    paste0("log_", env_name, "_", task_name, ".txt")
  )

  log_con <- file(log_path, open = "wt")
  sink(log_con, type = "output")
  sink(log_con, type = "message")

  # when the main closes correctly or with an error state we close all files (even the log)
  on.exit(closeAllConnections(), add = TRUE)

  dataout_server <- trend_analysis(
    exclude_list = config$exclude_server_cols,
    csv_path = server_csv,
    task_name = task_name,
    env_name = env_name,
    tz = config$tz,
    plot_dir = path_plots
  )

  dataout_client <- trend_analysis(
    exclude_list = config$exclude_client_cols,
    csv_path = client_csv,
    task_name = task_name,
    env_name = env_name,
    aggregate = TRUE,
    tz = config$tz,
    plot_dir = path_plots
  )

  readr::write_csv(
    dataout_server,
    file.path(config$output_dir, paste(env_name, task_name, "data_test_server.csv", sep = "_"))
  )

  readr::write_csv(
    dataout_client,
    file.path(config$output_dir, paste(env_name, task_name, "data_test_client.csv", sep = "_"))
  )

}

main()